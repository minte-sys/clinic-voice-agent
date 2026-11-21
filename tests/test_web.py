"""Talk-or-type options, prompts per mode, and the local web server."""
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import web_server
from clinic.options import job_options
from clinic.prompts import build_instructions, greeting_instructions

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=ZoneInfo("Asia/Dubai"))


# ---- options from job metadata ---------------------------------------------------

@pytest.mark.parametrize("metadata, expected", [
    ('{"mode": "text", "language": "ru"}', ("text", "ru", "ru")),
    ('{"mode": "voice", "language": "zh"}', ("voice", "zh", "zh")),
    ('{"mode": "text"}', ("text", "ar", None)),
    ('{"mode": "video", "language": "fr"}', ("voice", "ar", None)),   # unknown values
    ('{"language": ["en"]}', ("voice", "ar", None)),                  # wrong type
    ("", ("voice", "ar", None)),                                      # Agent Console session
    (None, ("voice", "ar", None)),
    ("not json", ("voice", "ar", None)),
    ('["text"]', ("voice", "ar", None)),
])
def test_job_options(metadata, expected):
    assert job_options(metadata, "ar") == expected


# ---- prompts ---------------------------------------------------------------------

def test_instructions_differ_by_mode_and_share_the_rules():
    voice = build_instructions(NOW, "Test Clinic", "Asia/Dubai", "ar", "voice")
    text = build_instructions(NOW, "Test Clinic", "Asia/Dubai", "ar", "text")
    assert "spoken phone call" in voice and "text chat" not in voice
    assert "text chat" in text and "spoken phone call" not in text
    for prompt in (voice, text):
        assert "Tuesday, 2026-11-10" in prompt
        assert "starts in Arabic" in prompt
        assert "Never give medical advice" in prompt
        assert "{" not in prompt                      # every placeholder was filled


def test_greeting_is_one_language_and_skips_recording_notice_in_text():
    assert "Hindi only" in greeting_instructions("hi", "voice")
    assert "recorded" in greeting_instructions("hi", "voice")
    assert "recorded" not in greeting_instructions("hi", "text")
    assert "English only" in greeting_instructions("fr", "voice")          # unknown -> English


def test_greeting_offers_other_languages_when_none_was_picked():
    text = greeting_instructions("ar", "voice", offer_languages=True)
    assert "Arabic only" in text and "still in Arabic" in text
    assert "English, Hindi, Mandarin Chinese, Russian" in text
    assert "can also speak" not in greeting_instructions("ar", "voice")


# ---- web server ------------------------------------------------------------------

def test_clean_choice_keeps_only_known_values():
    assert web_server.clean_choice({"mode": "text", "language": "ar"}) == {"mode": "text", "language": "ar"}
    assert web_server.clean_choice({"mode": "x", "language": "fr", "extra": 1}) == {"mode": "voice", "language": None}
    assert web_server.clean_choice({}) == {"mode": "voice", "language": None}


@pytest.fixture(autouse=True)
def fresh_limits(monkeypatch):
    monkeypatch.setattr(web_server, "PER_VISITOR", web_server.RateLimiter(5, 600))
    monkeypatch.setattr(web_server, "EVERYONE", web_server.RateLimiter(60, 3600))


@pytest.fixture
def base_url():
    httpd = web_server.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def post(url, data: bytes):
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request) as reply:
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_page_is_served(base_url):
    with urllib.request.urlopen(base_url + "/") as reply:
        page = reply.read().decode()
    assert reply.status == 200
    assert 'id="talk"' in page and 'id="type"' in page


def test_token_endpoint_returns_token_and_choice(base_url, monkeypatch):
    seen = {}

    def fake_create_token(choice):
        seen.update(choice)
        return {"url": "wss://example.invalid", "token": "t", **choice}

    monkeypatch.setattr(web_server, "create_token", fake_create_token)
    status, body = post(base_url + "/token", b'{"mode": "text", "language": "zh", "junk": true}')
    assert status == 200
    assert body == {"url": "wss://example.invalid", "token": "t", "mode": "text", "language": "zh"}
    assert seen == {"mode": "text", "language": "zh"}


def test_token_endpoint_reports_missing_settings(base_url, monkeypatch):
    def missing(choice):
        raise KeyError("LIVEKIT_API_KEY")

    monkeypatch.setattr(web_server, "create_token", missing)
    status, body = post(base_url + "/token", b"{}")
    assert status == 500 and "LIVEKIT_API_KEY is not set" in body["error"]


def test_token_endpoint_rejects_bad_requests(base_url):
    assert post(base_url + "/token", b"not json")[0] == 400
    assert post(base_url + "/token", b"[1, 2]")[0] == 400
    assert post(base_url + "/other", b"{}")[0] == 404


def get(url):
    with urllib.request.urlopen(url) as reply:
        return reply.status, reply.headers, reply.read().decode()


def test_health_and_security_headers(base_url):
    status, headers, body = get(base_url + "/health")
    assert (status, body) == (200, "ok")
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Content-Security-Policy"] == "frame-ancestors *"


def test_frame_ancestors_can_be_restricted(base_url, monkeypatch):
    monkeypatch.setenv("FRAME_ANCESTORS", "https://myclinic.example")
    assert get(base_url + "/")[1]["Content-Security-Policy"] == "frame-ancestors https://myclinic.example"


def test_widget_and_demo_site_are_served(base_url):
    status, headers, script = get(base_url + "/widget.js")
    assert status == 200 and "javascript" in headers["Content-Type"]
    assert "iframe" in script
    status, _, page = get(base_url + "/demo-site.html")
    assert status == 200 and "widget.js" in page


def test_token_endpoint_limits_each_visitor(base_url, monkeypatch):
    monkeypatch.setattr(web_server, "create_token", lambda choice: {"token": "t", **choice})
    monkeypatch.setattr(web_server, "PER_VISITOR", web_server.RateLimiter(2, 600))
    assert post(base_url + "/token", b"{}")[0] == 200
    assert post(base_url + "/token", b"{}")[0] == 200
    status, body = post(base_url + "/token", b"{}")
    assert status == 429 and "10 minutes" in body["error"]


def test_token_endpoint_global_limit(base_url, monkeypatch):
    monkeypatch.setattr(web_server, "create_token", lambda choice: {"token": "t", **choice})
    monkeypatch.setattr(web_server, "EVERYONE", web_server.RateLimiter(1, 3600))
    assert post(base_url + "/token", b"{}")[0] == 200
    status, body = post(base_url + "/token", b"{}")
    assert status == 429 and "busy" in body["error"]


def test_forwarded_address_only_trusted_behind_proxy(base_url, monkeypatch):
    monkeypatch.setattr(web_server, "create_token", lambda choice: {"token": "t", **choice})
    monkeypatch.setattr(web_server, "PER_VISITOR", web_server.RateLimiter(1, 600))

    def from_ip(ip):
        request = urllib.request.Request(base_url + "/token", data=b"{}",
                                         headers={"X-Forwarded-For": ip + ", 10.0.0.1"})
        try:
            with urllib.request.urlopen(request) as reply:
                return reply.status
        except urllib.error.HTTPError as err:
            return err.code

    # Without TRUST_PROXY the header is ignored: both requests count as the same visitor.
    assert (from_ip("1.1.1.1"), from_ip("2.2.2.2")) == (200, 429)
    monkeypatch.setenv("TRUST_PROXY", "1")
    monkeypatch.setattr(web_server, "PER_VISITOR", web_server.RateLimiter(1, 600))
    assert (from_ip("1.1.1.1"), from_ip("2.2.2.2"), from_ip("1.1.1.1")) == (200, 200, 429)


def test_rate_limiter_window():
    now = [0.0]
    limiter = web_server.RateLimiter(2, 60, clock=lambda: now[0])
    assert limiter.allow("a") and limiter.allow("a") and not limiter.allow("a")
    assert limiter.allow("b")                       # keys are separate
    assert limiter.retry_after("a") == 61
    now[0] = 60.0
    assert limiter.allow("a") and limiter.retry_after("c") == 0


@pytest.mark.parametrize("env, expected", [
    ({}, ("127.0.0.1", 8000)),
    ({"WEB_PORT": "9000"}, ("127.0.0.1", 9000)),
    ({"PORT": "10000"}, ("0.0.0.0", 10000)),          # hosted, e.g. Render
    ({"PORT": "10000", "WEB_HOST": "127.0.0.1"}, ("127.0.0.1", 10000)),
])
def test_listen_address(env, expected):
    assert web_server.listen_address(env) == expected
