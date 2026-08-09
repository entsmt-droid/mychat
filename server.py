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


def get_users():
    conn = db()

    rows = conn.execute(
        "SELECT username FROM users ORDER BY username"
    ).fetchall()

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


async def broadcast_users():
    users = get_users()

    online = set(clients.values())

    data = {
        "type": "users",
        "users": [
            {
                "username": user,
                "online": user in online
            }
            for user in users
        ]
    }

    for client in list(clients.keys()):
        try:
            await send_json(client, data)
        except:
            pass


async def websocket_chat(request):

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    username = None

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

                success = register_user(username, password)

                await send_json(ws, {
                    "type": "register",
                    "success": success,
                    "message":
                        "Registration successful"
                        if success
                        else "Username already exists"
                })

                if success:
                    await broadcast_users()

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

                    await send_json(ws, {
                        "type": "users",
                        "users": [
                            {
                                "username": user,
                                "online": user in set(clients.values())
                            }
                            for user in get_users()
                        ]
                    })

                    await broadcast_users()

                else:

                    await send_json(ws, {
                        "type": "login",
                        "success": False,
                        "message": "Wrong username or password"
                    })

            # GET HISTORY
            elif action == "history":

                if ws not in clients:
                    continue

                other = data.get("username", "").strip()

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

            # MARK SEEN
            elif action == "seen":

                if ws not in clients:
                    continue

                viewer = clients[ws]
                sender = data.get("username", "").strip()

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

                # Sender ko seen status bhejo
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

                receiver = data.get(
                    "receiver", ""
                ).strip()

                text = data.get(
                    "message", ""
                ).strip()

                if not receiver or not text:
                    continue

                sender = clients[ws]

                conn = db()

                cursor = conn.execute("""
                    INSERT INTO messages
                    (sender, receiver, message, seen)
                    VALUES (?, ?, ?, 0)
                """, (sender, receiver, text))

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

                # Receiver ko message
                for client, user in list(clients.items()):

                    if user == receiver:

                        try:
                            await send_json(
                                client,
                                message_data
                            )
                        except:
                            pass

                # Sender ko bhi message
                await send_json(
                    ws,
                    message_data
                )

    finally:

        if ws in clients:

            clients.pop(ws)

            await broadcast_users()

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
