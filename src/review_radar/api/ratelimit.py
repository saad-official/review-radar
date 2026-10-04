"""Runs per hour per client, enforced twice (copied from Changelog Forge).

  1. An in-process token bucket: free, instant, and stops a burst before it touches the
     database or GitHub. Per instance only - on a serverless platform each instance has
     its own bucket, so on its own it is a speed bump, not a limit.
  2. A Postgres count of runs created by this client in the last hour: the real limit,
     shared by every instance.

Clients are identified by a salted SHA-256 of their IP: enough to count, useless to anyone
who reads the table.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable

from fastapi import Request

from ..config import AppSettings


class TokenBucket:
    def __init__(
        self,
        capacity: int,
        per_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.capacity = capacity
        self.rate = capacity / per_seconds
        self.clock = clock
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> tuple[bool, float]:
        """Take one token. Returns (allowed, seconds until the next token)."""
        with self._lock:
            now = self.clock()
            tokens, last = self._state.get(key, (float(self.capacity), now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens >= 1:
                self._state[key] = (tokens - 1, now)
                return True, 0.0
            self._state[key] = (tokens, now)
            return False, (1 - tokens) / self.rate


def client_ip(request: Request, settings: AppSettings) -> str:
    if settings.trust_proxy_headers or settings.vercel:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()
    return request.client.host if request.client else "unknown"


def client_key(ip: str, settings: AppSettings) -> str:
    salt = settings.ip_hash_salt.get_secret_value()
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:32]
