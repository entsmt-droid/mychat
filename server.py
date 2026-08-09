import os
import json
import sqlite3
import asyncio
from hashlib import sha256
from aiohttp import web

DB = "chat.db"
clients = {}


def db():
    return sqlite3.connect(DB)


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT NOT NULL,
            receiver TEXT NOT NULL,
            message TEXT NOT NULL,
            seen INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def hash_password(password):
    return sha256(password.encode()).hexdigest()


def register_user(username, password):
    try:
        conn = db()
        conn.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)",
            (username, hash_password(password))
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False


def login_user(username, password):
    conn = db()

    row = conn.execute(
        "SELECT id FROM users WHERE username=? AND password=?",
        (username, hash_password(password))
    ).fetchone()

    conn.close()

    return row is not None


def user_exists(username):
    conn = db()

    row = conn.execute(
        "SELECT id FROM users WHERE username=?",
        (username,)
    ).fetchone()

    conn.close()

    return row is not None


def get_chat_users(username):
    conn = db()

    rows = conn.execute("""
        SELECT
            CASE
                WHEN sender=? THEN receiver
                ELSE sender
            END AS chat_user,
            MAX(id) AS last_id
        FROM messages
        WHERE sender=? OR receiver=?
        GROUP BY chat_user
        ORDER BY last_id DESC
    """, (username, username, username)).fetchall()

    conn.close()

    return [row[0] for row in rows]


def get_history(user1, user2):
    conn = db()

    rows = conn.execute("""
        SELECT id, sender, receiver, message, seen, created_at
        FROM messages
        WHERE
            (sender=? AND receiver=?)
            OR
            (sender=? AND receiver=?)
        ORDER BY id ASC
    """, (user1, user2, user2, user1)).fetchall()

    conn.close()

    return [
        {
            "id": row[0],
            "sender": row[1],
            "receiver": row[2],
            "message": row[3],
            "seen": bool(row[4]),
            "created_at": row[5]
        }
        for row in rows
    ]


async def send_json(ws, data):
    await ws.send_str(json.dumps(data))


async def send_chat_list(ws, username):
    chat_users = get_chat_users(username)
    online_users = set(clients.values())

    chats = []

    for user in chat_users:
        chats.append({
            "username": user,
            "online": user in online_users
        })

    await send_json(ws, {
        "type": "chat_list",
        "chats": chats
    })


async def broadcast_chat_lists():
    for ws, username in list(clients.items()):
        try:
            await send_chat_list(ws, username)
        except:
            pass


async def websocket_chat(request):

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    try:

        async for msg in ws:

            if msg.type != web.WSMsgType.TEXT:
                continue

            try:
                data = json.loads(msg.data)
            except:
                continue

            action = data.get("action")

            # REGISTER
            if action == "register":

                username = data.get("username", "").strip()
                password = data.get("password", "")

                if not username or not password:

                    await send_json(ws, {
                        "type": "register",
                        "success": False,
                        "message": "Username and password required"
                    })

                    continue

                success = register_user(
                    username,
                    password
                )

                await send_json(ws, {
                    "type": "register",
                    "success": success,
                    "message":
                        "Registration successful"
                        if success
                        else "Username already exists"
                })

            # LOGIN
            elif action == "login":

                username = data.get("username", "").strip()
                password = data.get("password", "")

                if login_user(username, password):

                    clients[ws] = username

                    await send_json(ws, {
                        "type": "login",
                        "success": True,
                        "username": username
                    })

                    await send_chat_list(
                        ws,
                        username
                    )

                    await broadcast_chat_lists()

                else:

                    await send_json(ws, {
                        "type": "login",
                        "success": False,
                        "message": "Wrong username or password"
                    })

            # NEW CHAT
            elif action == "new_chat":

                if ws not in clients:
                    continue

                username = data.get(
                    "username",
                    ""
                ).strip()

                if not username:

                    await send_json(ws, {
                        "type": "new_chat",
                        "success": False,
                        "message": "Username required"
                    })

                    continue

                if username == clients[ws]:

                    await send_json(ws, {
                        "type": "new_chat",
                        "success": False,
                        "message": "You cannot chat with yourself"
                    })

                    continue

                if not user_exists(username):

                    await send_json(ws, {
                        "type": "new_chat",
                        "success": False,
                        "message": "User not found"
                    })

                    continue

                await send_json(ws, {
                    "type": "new_chat",
                    "success": True,
                    "username": username
                })

            # HISTORY
            elif action == "history":

                if ws not in clients:
                    continue

                other = data.get(
                    "username",
                    ""
                ).strip()

                if not other:
                    continue

                history = get_history(
                    clients[ws],
                    other
                )

                await send_json(ws, {
                    "type": "history",
                    "username": other,
                    "messages": history
                })

            # SEEN
            elif action == "seen":

                if ws not in clients:
                    continue

                viewer = clients[ws]

                sender = data.get(
                    "username",
                    ""
                ).strip()

                if not sender:
                    continue

                conn = db()

                conn.execute("""
                    UPDATE messages
                    SET seen=1
                    WHERE sender=?
                    AND receiver=?
                    AND seen=0
                """, (sender, viewer))

                conn.commit()
                conn.close()

                # Sender ko Seen status
                for client, user in list(clients.items()):

                    if user == sender:

                        try:
                            await send_json(client, {
                                "type": "seen",
                                "username": viewer
                            })
                        except:
                            pass

            # MESSAGE
            elif action == "message":

                if ws not in clients:
                    continue

                sender = clients[ws]

                receiver = data.get(
                    "receiver",
                    ""
                ).strip()

                text = data.get(
                    "message",
                    ""
                ).strip()

                if not receiver or not text:
                    continue

                if not user_exists(receiver):
                    await send_json(ws, {
                        "type": "error",
                        "message": "User not found"
                    })
                    continue

                conn = db()

                cursor = conn.execute("""
                    INSERT INTO messages
                    (sender, receiver, message, seen)
                    VALUES (?, ?, ?, 0)
                """, (
                    sender,
                    receiver,
                    text
                ))

                message_id = cursor.lastrowid

                conn.commit()
                conn.close()

                message_data = {
                    "type": "message",
                    "id": message_id,
                    "sender": sender,
                    "receiver": receiver,
                    "message": text,
                    "seen": False
                }

                # Receiver ko
                for client, user in list(clients.items()):

                    if user == receiver:

                        try:
                            await send_json(
                                client,
                                message_data
                            )

                            await send_chat_list(
                                client,
                                receiver
                            )

                        except:
                            pass

                # Sender ko
                await send_json(
                    ws,
                    message_data
                )

                # Sender ki chat list update
                await send_chat_list(
                    ws,
                    sender
                )

            # LOGOUT / DISCONNECT
            elif action == "logout":

                if ws in clients:
                    clients.pop(ws)

                await broadcast_chat_lists()

    finally:

        if ws in clients:
            clients.pop(ws)

        await broadcast_chat_lists()

    return ws


async def index(request):

    try:

        with open(
            "index.html",
            "r",
            encoding="utf-8"
        ) as f:

            html = f.read()

        return web.Response(
            text=html,
            content_type="text/html"
        )

    except Exception as e:

        return web.Response(
            text=str(e),
            status=500
        )


async def main():

    init_db()

    app = web.Application()

    app.router.add_get(
        "/",
        index
    )

    app.router.add_get(
        "/ws",
        websocket_chat
    )

    port = int(
        os.environ.get(
            "PORT",
            8765
        )
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    print(
        f"MyChat running on port {port}"
    )

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
