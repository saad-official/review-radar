"""How a queued run gets processed, given where the API is running.

    "qstash"      QSTASH_TOKEN + PUBLIC_API_URL set: publish a message to Upstash QStash,
                  which POSTs /api/runs/{id}/process (signed) and retries on failure.
                  The serverless-correct option: the work runs in its own request with its
                  own time limit, and a crash is retried by the queue.
    "background"  not on Vercel (local, Docker, Render): FastAPI BackgroundTasks in the
                  same process, after the 202 is sent.
    "client"      on Vercel without QStash: nothing runs after the response is sent, so
                  the client (the web UI) POSTs /process itself, then follows /events.

`/process` is idempotent in every mode (the Store lease), so a QStash delivery and a client
call racing each other is harmless: one wins, the other gets "already being processed".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Literal

import httpx

from .config import AppSettings

Mode = Literal["qstash", "background", "client"]


def dispatch_mode(settings: AppSettings) -> Mode:
    if settings.qstash_token and settings.public_api_url:
        return "qstash"
    return "client" if settings.vercel else "background"


def process_url(settings: AppSettings, run_id: str) -> str:
    base = (settings.public_api_url or "").rstrip("/")
    return f"{base}/api/runs/{run_id}/process"


def publish_to_qstash(
    settings: AppSettings, run_id: str, *, client: httpx.Client | None = None
) -> bool:
    assert settings.qstash_token is not None
    http = client or httpx.Client(timeout=10.0)
    try:
        response = http.post(
            f"{settings.qstash_url.rstrip('/')}/v2/publish/{process_url(settings, run_id)}",
            headers={
                "Authorization": f"Bearer {settings.qstash_token.get_secret_value()}",
                "Content-Type": "application/json",
                "Upstash-Retries": "2",
            },
            content=json.dumps({"run_id": run_id}),
        )
        return response.status_code < 300
    except httpx.HTTPError:
        return False
    finally:
        if client is None:
            http.close()


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def verify_qstash_signature(
    signature: str, body: bytes, url: str, keys: list[str], *, now: float | None = None
) -> bool:
    """Verify an `Upstash-Signature` JWT (HS256) by hand: issuer, subject (our URL), time
    window, and the SHA-256 of the exact body. Tries the current then the next signing key,
    which is how QStash rotates keys without downtime."""
    try:
        header_b64, payload_b64, signature_b64 = signature.split(".")
        header = json.loads(_b64url_decode(header_b64))
        claims = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return False
    if header.get("alg") != "HS256":
        return False
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    provided = _b64url_decode(signature_b64)
    if not any(
        hmac.compare_digest(
            hmac.new(key.encode(), signing_input, hashlib.sha256).digest(), provided
        )
        for key in keys
        if key
    ):
        return False
    current = now if now is not None else time.time()
    if claims.get("iss") != "Upstash":
        return False
    if claims.get("sub") != url:
        return False
    if "exp" in claims and current > float(claims["exp"]):
        return False
    if "nbf" in claims and current < float(claims["nbf"]) - 5:
        return False
    expected = _b64url(hashlib.sha256(body).digest())
    return str(claims.get("body", "")).rstrip("=") == expected


def sign_for_tests(claims: dict[str, object], key: str) -> str:
    """Build a QStash-shaped JWT. Used by tests; kept here so both sides share encoding."""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps(claims).encode())
    digest = hmac.new(key.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(digest)}"


def body_hash(body: bytes) -> str:
    return _b64url(hashlib.sha256(body).digest())
