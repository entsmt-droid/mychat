import asyncio
import json
import sqlite3
import hashlib
import websockets
import os

DB = "chat.db"
clients = {}


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


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
    await ws.send(json.dumps(data))


async def chat(websocket):
    username = None

    try:
        async for data in websocket:

            try:
                request = json.loads(data)
            except:
                continue

            action = request.get("action")

            # REGISTER
            if action == "register":
                username = request.get("username", "").strip()
                password = request.get("password", "")

                if not username or not password:
                    await send_json(websocket, {
                        "type": "register",
                        "success": False,
                        "message": "Username and password required"
                    })
                    continue

                success = register_user(username, password)

                await send_json(websocket, {
                    "type": "register",
                    "success": success,
                    "message": "Registration successful"
                               if success
                               else "Username already exists"
                })

            # LOGIN
            elif action == "login":
                username = request.get("username", "").strip()
                password = request.get("password", "")

                if login_user(username, password):
                    clients[websocket] = username

                    await send_json(websocket, {
                        "type": "login",
                        "success": True,
                        "username": username
                    })

                    print(username, "logged in")

                else:
                    await send_json(websocket, {
                        "type": "login",
                        "success": False,
                        "message": "Wrong username or password"
                    })

            # MESSAGE
            elif action == "message":

                if websocket not in clients:
                    continue

                receiver = request.get("receiver", "").strip()
                text = request.get("message", "").strip()

                if not receiver or not text:
                    continue

                sender = clients[websocket]

                conn = sqlite3.connect(DB)
                conn.execute(
                    "INSERT INTO messages (sender, receiver, message) VALUES (?, ?, ?)",
                    (sender, receiver, text)
                )
                conn.commit()
                conn.close()

                # Receiver online hai to message bhejo
                for client, user in list(clients.items()):
                    if user == receiver:
                        await send_json(client, {
                            "type": "message",
                            "sender": sender,
                            "receiver": receiver,
                            "message": text
                        })

                # Sender ko bhi message dikhao
                await send_json(websocket, {
                    "type": "message",
                    "sender": sender,
                    "receiver": receiver,
                    "message": text
                })

    except websockets.exceptions.ConnectionClosed:
        pass

    finally:
        if websocket in clients:
            left_user = clients.pop(websocket)
            print(left_user, "disconnected")


async def main():
    port = int(os.environ.get("PORT", 8765))

    async with websockets.serve(chat, "0.0.0.0", port):
        print(f"Chat server started on port {port}")
        await asyncio.Future()


asyncio.run(main())
