import os
import json
import sqlite3
import asyncio
from hashlib import sha256

from aiohttp import web

DB = "chat.db"
clients = {}


def init_db():
    conn = sqlite3.connect(DB)

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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def hash_password(password):
    return sha256(password.encode()).hexdigest()


def register_user(username, password):
    try:
        conn = sqlite3.connect(DB)

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
    conn = sqlite3.connect(DB)

    row = conn.execute(
        "SELECT id FROM users WHERE username=? AND password=?",
        (username, hash_password(password))
    ).fetchone()

    conn.close()

    return row is not None


async def send_json(ws, data):
    await ws.send_str(json.dumps(data))


async def websocket_chat(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    username = None

    try:
        async for msg in ws:

            if msg.type != web.WSMsgType.TEXT:
                continue

            try:
                request_data = json.loads(msg.data)
            except:
                continue

            action = request_data.get("action")

            # REGISTER
            if action == "register":

                username = request_data.get(
                    "username", ""
                ).strip()

                password = request_data.get(
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

                username = request_data.get(
                    "username", ""
                ).strip()

                password = request_data.get(
                    "password", ""
                )

                if login_user(username, password):

                    clients[ws] = username

                    await send_json(ws, {
                        "type": "login",
                        "success": True,
                        "username": username
                    })

                    print(username, "logged in")

                else:

                    await send_json(ws, {
                        "type": "login",
                        "success": False,
                        "message":
                            "Wrong username or password"
                    })

            # MESSAGE
            elif action == "message":

                if ws not in clients:
                    continue

                receiver = request_data.get(
                    "receiver", ""
                ).strip()

                text = request_data.get(
                    "message", ""
                ).strip()

                if not receiver or not text:
                    continue

                sender = clients[ws]

                conn = sqlite3.connect(DB)

                conn.execute(
                    """
                    INSERT INTO messages
                    (sender, receiver, message)
                    VALUES (?, ?, ?)
                    """,
                    (sender, receiver, text)
                )

                conn.commit()
                conn.close()

                message_data = {
                    "type": "message",
                    "sender": sender,
                    "receiver": receiver,
                    "message": text
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

            left_user = clients.pop(ws)

            print(left_user, "disconnected")

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
            text=f"index.html error: {e}",
            status=500
        )


async def main():

    init_db()

    app = web.Application()

    app.router.add_get("/", index)

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
