"""Local web server for the clinic assistant page.

Run:   python web_server.py      then open http://localhost:8000

It does two things:
  * serves the page in web/
  * POST /token: creates a LiveKit room token that also dispatches the clinic
    agent into the room, carrying the caller's choice (talk or type, language)

For local testing only. It listens on this computer alone and has no login:
anyone who can reach it can start a conversation on your LiveKit account. Put
real authentication and rate limits in front of /token before hosting it.
"""
from __future__ import annotations

import json
import os
import secrets
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env.local")

WEB_DIR = Path(__file__).parent / "web"
AGENT_NAME = "clinic-agent"
LANGUAGES = ("en", "ar", "hi", "zh", "ru")
HOST = os.getenv("WEB_HOST", "127.0.0.1")
PORT = int(os.getenv("WEB_PORT", "8000"))


def clean_choice(body: dict) -> dict:
    """Keep only values the agent understands."""
    mode = "text" if body.get("mode") == "text" else "voice"
    language = body.get("language") if body.get("language") in LANGUAGES else None
    return {"mode": mode, "language": language}


def create_token(choice: dict) -> dict:
    from livekit import api  # imported here so the page still loads if LiveKit is missing

    room = f"clinic-{secrets.token_hex(4)}"
    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(f"caller-{secrets.token_hex(4)}")
        .with_name("Caller")
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .with_room_config(
            api.RoomConfiguration(
                agents=[api.RoomAgentDispatch(agent_name=AGENT_NAME, metadata=json.dumps(choice))]
            )
        )
        .to_jwt()
    )
    return {"url": os.environ["LIVEKIT_URL"], "token": token, **choice}


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/token":
            self._json(404, {"error": "not found"})
            return
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except ValueError:
            self._json(400, {"error": "send a JSON object"})
            return
        try:
            self._json(200, create_token(clean_choice(body)))
        except KeyError as missing:
            self._json(500, {"error": f"{missing.args[0]} is not set in .env.local"})
        except Exception as exc:  # report the cause to the page instead of a blank failure
            self._json(500, {"error": f"could not create a token: {exc}"})

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def make_server(host: str = HOST, port: int = PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), partial(Handler, directory=str(WEB_DIR)))


if __name__ == "__main__":
    httpd = make_server()
    print(f"Clinic assistant page: http://localhost:{PORT}   (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
