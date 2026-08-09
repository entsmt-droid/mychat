import os
import json
import sqlite3
import asyncio
from hashlib import sha256
from aiohttp import web

DB = "chat.db"
clients = {}
typing_users = {}


def db():
    return sqlite3.connect(DB)


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            photo TEXT DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT NOT NULL,
            receiver TEXT NOT NULL,
            message TEXT NOT NULL,
            seen INTEGER DEFAULT 0,
            deleted_for_sender INTEGER DEFAULT 0,
            deleted_for_receiver INTEGER DEFAULT 0,
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
            "INSERT INTO users (username,password) VALUES (?,?)",
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


def get_chats(username):
    conn = db()

    rows = conn.execute("""
        SELECT
        CASE
            WHEN sender=? THEN receiver
            ELSE sender
        END AS chat_user,
        MAX(id)
        FROM messages
        WHERE sender=? OR receiver=?
        GROUP BY chat_user
        ORDER BY MAX(id) DESC
    """, (username, username, username)).fetchall()

    conn.close()

    return [x[0] for x in rows]


def get_history(user1, user2):
    conn = db()

    rows = conn.execute("""
        SELECT
            id,
            sender,
            receiver,
            message,
            seen,
            deleted_for_sender,
            deleted_for_receiver,
            created_at
        FROM messages
        WHERE
            (sender=? AND receiver=?)
            OR
            (sender=? AND receiver=?)
        ORDER BY id ASC
    """, (user1, user2, user2, user1)).fetchall()

    conn.close()

    result = []

    for r in rows:

        if r[1] == user1:
            deleted = bool(r[5])
        else:
            deleted = bool(r[6])

        if deleted:
            continue

        result.append({
            "id": r[0],
            "sender": r[1],
            "receiver": r[2],
            "message": r[3],
            "seen": bool(r[4]),
            "time": r[7]
        })

    return result


async def send_json(ws, data):
    await ws.send_str(json.dumps(data))


async def send_chat_list(ws, username):

    chats = get_chats(username)
    online = set(clients.values())

    await send_json(ws, {
        "type": "chat_list",
        "chats": [
            {
                "username": user,
                "online": user in online
            }
            for user in chats
        ]
    })


async def broadcast_lists():

    for ws, username in list(clients.items()):

        try:
            await send_chat_list(ws, username)

        except Exception:
            pass


async def notify_user(username, data):

    for ws, user in list(clients.items()):

        if user == username:

            try:
                await send_json(ws, data)

            except Exception:
                pass


async def websocket_handler(request):

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    try:

        async for msg in ws:

            if msg.type != web.WSMsgType.TEXT:
                continue

            try:
                data = json.loads(msg.data)
            except Exception:
                continue

            action = data.get("action")


            # REGISTER
            if action == "register":

                username = data.get(
                    "username", ""
                ).strip()

                password = data.get(
                    "password", ""
                )

                if not username or not password:

                    await send_json(ws, {
                        "type": "register",
                        "success": False,
                        "message":
                            "Username and password required"
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

                username = data.get(
                    "username", ""
                ).strip()

                password = data.get(
                    "password", ""
                )


                if not login_user(
                    username,
                    password
                ):

                    await send_json(ws, {
                        "type": "login",
                        "success": False,
                        "message":
                            "Wrong username or password"
                    })

                    continue


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

                await broadcast_lists()


            # NEW CHAT
            elif action == "new_chat":

                if ws not in clients:
                    continue


                username = data.get(
                    "username", ""
                ).strip()


                if not user_exists(username):

                    await send_json(ws, {
                        "type": "new_chat",
                        "success": False,
                        "message": "User not found"
                    })

                    continue


                if username == clients[ws]:

                    await send_json(ws, {
                        "type": "new_chat",
                        "success": False,
                        "message":
                            "You cannot chat with yourself"
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
                    "username", ""
                ).strip()


                history = get_history(
                    clients[ws],
                    other
                )


                await send_json(ws, {
                    "type": "history",
                    "username": other,
                    "messages": history
                })


            # MESSAGE
            elif action == "message":

                if ws not in clients:
                    continue


                sender = clients[ws]

                receiver = data.get(
                    "receiver", ""
                ).strip()

                text = data.get(
                    "message", ""
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

                cur = conn.execute("""
                    INSERT INTO messages
                    (sender,receiver,message)
                    VALUES (?,?,?)
                """, (
                    sender,
                    receiver,
                    text
                ))

                message_id = cur.lastrowid

                conn.commit()
                conn.close()


                packet = {
                    "type": "message",
                    "id": message_id,
                    "sender": sender,
                    "receiver": receiver,
                    "message": text,
                    "seen": False
                }


                await send_json(ws, packet)

                await notify_user(
                    receiver,
                    packet
                )


                await broadcast_lists()


            # SEEN
            elif action == "seen":

                if ws not in clients:
                    continue


                viewer = clients[ws]

                sender = data.get(
                    "username", ""
                ).strip()


                conn = db()

                conn.execute("""
                    UPDATE messages
                    SET seen=1
                    WHERE sender=?
                    AND receiver=?
                    AND seen=0
                """, (
                    sender,
                    viewer
                ))

                conn.commit()
                conn.close()


                await notify_user(
                    sender,
                    {
                        "type": "seen",
                        "username": viewer
                    }
                )


            # TYPING
            elif action == "typing":

                if ws not in clients:
                    continue


                sender = clients[ws]

                receiver = data.get(
                    "receiver", ""
                ).strip()

                is_typing = bool(
                    data.get("typing")
                )


                if is_typing:

                    typing_users[
                        sender
                    ] = receiver

                else:

                    typing_users.pop(
                        sender,
                        None
                    )


                await notify_user(
                    receiver,
                    {
                        "type": "typing",
                        "username": sender,
                        "typing": is_typing
                    }
                )


            # DELETE ONE MESSAGE FOR ME
            elif action == "delete_for_me":

                if ws not in clients:
                    continue


                username = clients[ws]

                message_id = data.get(
                    "id"
                )


                conn = db()

                row = conn.execute("""
                    SELECT sender,receiver
                    FROM messages
                    WHERE id=?
                """, (
                    message_id,
                )).fetchone()


                if not row:

                    conn.close()
                    continue


                sender, receiver = row


                if username == sender:

                    conn.execute("""
                        UPDATE messages
                        SET deleted_for_sender=1
                        WHERE id=?
                    """, (message_id,))

                elif username == receiver:

                    conn.execute("""
                        UPDATE messages
                        SET deleted_for_receiver=1
                        WHERE id=?
                    """, (message_id,))

                else:

                    conn.close()
                    continue


                conn.commit()
                conn.close()


                await send_json(ws, {
                    "type": "message_deleted",
                    "id": message_id
                })


            # DELETE ONE MESSAGE FOR BOTH
            elif action == "delete_for_both":

                if ws not in clients:
                    continue


                username = clients[ws]

                message_id = data.get(
                    "id"
                )


                conn = db()

                row = conn.execute("""
                    SELECT sender,receiver
                    FROM messages
                    WHERE id=?
                """, (
                    message_id,
                )).fetchone()


                if not row:

                    conn.close()
                    continue


                sender, receiver = row


                if username != sender:

                    conn.close()
                    continue


                conn.execute("""
                    DELETE FROM messages
                    WHERE id=?
                """, (message_id,))

                conn.commit()
                conn.close()


                await notify_user(
                    sender,
                    {
                        "type":
                            "message_deleted_both",
                        "id": message_id
                    }
                )


                await notify_user(
                    receiver,
                    {
                        "type":
                            "message_deleted_both",
                        "id": message_id
                    }
                )


            # DELETE COMPLETE CHAT FOR BOTH
            elif action == "delete_chat":

                if ws not in clients:
                    continue


                user1 = clients[ws]

                user2 = data.get(
                    "username", ""
                ).strip()


                if not user2:
                    continue


                conn = db()

                conn.execute("""
                    DELETE FROM messages
                    WHERE
                        (sender=? AND receiver=?)
                        OR
                        (sender=? AND receiver=?)
                """, (
                    user1,
                    user2,
                    user2,
                    user1
                ))

                conn.commit()
                conn.close()


                await notify_user(
                    user1,
                    {
                        "type":
                            "chat_deleted",
                        "username": user2
                    }
                )


                await notify_user(
                    user2,
                    {
                        "type":
                            "chat_deleted",
                        "username": user1
                    }
                )


                await broadcast_lists()


    except Exception as e:

        print(
            "WebSocket error:",
            e
        )


    finally:

        if ws in clients:

            username = clients.pop(ws)

            typing_users.pop(
                username,
                None
            )


        await broadcast_lists()


    return ws


async def index(request):

    try:

        with open(
            "index.html",
            encoding="utf-8"
        ) as f:

            return web.Response(
                text=f.read(),
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
        websocket_handler
    )


    port = int(
        os.environ.get(
            "PORT",
            "8765"
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
        "MyChat running on port",
        port
    )


    await asyncio.Event().wait()


if __name__ == "__main__":

    asyncio.run(main())
