"""Persistence: the Store protocol, an in-memory implementation, and Postgres + pgvector."""

from __future__ import annotations

from ..config import AppSettings
from .store import MemoryStore, Store

__all__ = ["MemoryStore", "Store", "make_store"]


def make_store(settings: AppSettings) -> Store:
    """Postgres when DATABASE_URL is set, otherwise a process-local MemoryStore (right for
    tests, evals and a keyless demo; wrong for a multi-instance deploy such as Vercel)."""
    if settings.database_url is not None and settings.database_url.get_secret_value():
        from .postgres import PostgresStore

        return PostgresStore(settings.database_url.get_secret_value())
    return MemoryStore()
