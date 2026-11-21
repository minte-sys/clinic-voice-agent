"""Web server for the clinic assistant: the page, the embed script and room tokens.

Run locally:   python web_server.py      then open http://localhost:8000
Hosted:        see "Put it online" in README.md (Render runs this same file)

What it serves:
  /                 the Talk or Type page (web/index.html)
  /widget.js        one-line embed for any website (web/widget.js)
  /demo-site.html   a sample clinic homepage with the widget on it
  /health           "ok", for the host's health check
  POST /token       a LiveKit room token that also dispatches the clinic agent,
                    carrying the caller's choice (talk or type, language)

Protection for a public demo (all set in the environment):
  TOKEN_LIMIT_PER_IP     conversations one visitor can start per 10 minutes (default 5)
  TOKEN_LIMIT_GLOBAL     conversations everyone together can start per hour (default 60)
  TRUST_PROXY=1          read the visitor's address from X-Forwarded-For (set on Render)
  FRAME_ANCESTORS        sites allowed to embed the page, e.g. "https://myclinic.com"
                         (default "*": any site, fine for a demo)
Tokens expire after 15 minutes. These limits cap cost; they are not a login.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

load_dotenv(".env.local")

WEB_DIR = Path(__file__).parent / "web"
AGENT_NAME = "clinic-agent"
LANGUAGES = ("en", "ar", "hi", "zh", "ru")
TOKEN_TTL = timedelta(minutes=15)


def listen_address(env=os.environ) -> tuple[str, int]:
    """Where to listen. Hosts such as Render set PORT and need 0.0.0.0."""
    if env.get("PORT"):
        return env.get("WEB_HOST", "0.0.0.0"), int(env["PORT"])
    return env.get("WEB_HOST", "127.0.0.1"), int(env.get("WEB_PORT", "8000"))


class RateLimiter:
    """Allow at most `limit` events per `window` seconds for each key."""

    def __init__(self, limit: int, window: float, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            events = self._events[key]
            while events and now - events[0] >= self.window:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True

    def retry_after(self, key: str) -> int:
        """Seconds until `key` may try again (0 if it may now)."""
        with self._lock:
            events = self._events.get(key)
            if not events or len(events) < self.limit:
                return 0
            return max(1, int(self.window - (self.clock() - events[0])) + 1)


PER_VISITOR = RateLimiter(int(os.getenv("TOKEN_LIMIT_PER_IP", "5")), 600)
EVERYONE = RateLimiter(int(os.getenv("TOKEN_LIMIT_GLOBAL", "60")), 3600)


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
        .with_ttl(TOKEN_TTL)
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
    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Content-Security-Policy",
                         f"frame-ancestors {os.getenv('FRAME_ANCESTORS', '*')}")
        super().end_headers()

    def do_GET(self) -> None:
        if self.path.split("?")[0] == "/health":
            self._send(200, b"ok", "text/plain")
            return
        super().do_GET()

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

        visitor = self.client_ip()
        if not PER_VISITOR.allow(visitor):
            minutes = -(-PER_VISITOR.retry_after(visitor) // 60)
            self._json(429, {"error": f"Too many conversations. Please try again in {minutes} minutes."})
            return
        if not EVERYONE.allow("all"):
            self._json(429, {"error": "The demo is busy right now. Please try again later."})
            return

        try:
            self._json(200, create_token(clean_choice(body)))
        except KeyError as missing:
            self._json(500, {"error": f"{missing.args[0]} is not set on the server"})
        except Exception as exc:  # report the cause to the page instead of a blank failure
            self._json(500, {"error": f"could not create a token: {exc}"})

    def client_ip(self) -> str:
        if os.getenv("TRUST_PROXY") == "1":
            forwarded = self.headers.get("X-Forwarded-For", "")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return self.client_address[0]

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _send(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def make_server(host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    default_host, default_port = listen_address()
    address = (default_host if host is None else host, default_port if port is None else port)
    return ThreadingHTTPServer(address, partial(Handler, directory=str(WEB_DIR)))


if __name__ == "__main__":
    httpd = make_server()
    host, port = httpd.server_address[:2]
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"Clinic assistant page: http://{shown}:{port}   (Ctrl+C to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
