"""`uv run migrate`: apply db/migrations/*.sql in order, once each.

Plain SQL files and a short runner instead of Alembic: the schema is small, the files are
readable on their own, and there is no ORM whose models need to stay in sync. Use the
*direct* (unpooled) Neon URL: DDL through PgBouncer's transaction mode is fragile.

One placeholder, `{{EMBEDDING_DIMENSIONS}}`, is filled from EMBEDDING_DIMENSIONS (default
768) because the vector columns' dimension is a deploy-time choice that must match the
embedder (docs/decisions/0005-voyage-embeddings.md). Set it before migrating.

    DATABASE_URL=<direct url> EMBEDDING_DIMENSIONS=1024 uv run migrate
"""

from __future__ import annotations

import sys
from importlib import resources

import psycopg

from ..config import get_settings


def migration_files() -> list[tuple[str, str]]:
    folder = resources.files("review_radar.db") / "migrations"
    return sorted(
        (entry.name, entry.read_text(encoding="utf-8"))
        for entry in folder.iterdir()
        if entry.name.endswith(".sql")
    )


def render(sql: str, dimensions: int) -> str:
    return sql.replace("{{EMBEDDING_DIMENSIONS}}", str(int(dimensions)))


def apply_migrations(dsn: str, dimensions: int = 768) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(dsn, prepare_threshold=None, autocommit=False) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS review_radar")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS review_radar.schema_migrations (
                   name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"""
        )
        conn.commit()
        done = {row[0] for row in conn.execute("SELECT name FROM review_radar.schema_migrations")}
        for name, sql in migration_files():
            if name in done:
                continue
            # One transaction per file: a failing migration leaves no half-applied schema.
            with conn.transaction():
                conn.execute(render(sql, dimensions))
                conn.execute(
                    "INSERT INTO review_radar.schema_migrations (name) VALUES (%s)", (name,)
                )
            applied.append(name)
    return applied


def _dsn() -> str:
    settings = get_settings()
    if settings.database_direct_url is not None and settings.database_direct_url.get_secret_value():
        return settings.database_direct_url.get_secret_value()
    if settings.database_url is None or not settings.database_url.get_secret_value():
        print(
            "DATABASE_URL (or DATABASE_DIRECT_URL) is not set (see .env.example).", file=sys.stderr
        )
        raise SystemExit(2)
    return settings.database_url.get_secret_value()


def main() -> None:
    dimensions = get_settings().embedding_dimensions
    applied = apply_migrations(_dsn(), dimensions)
    print(
        f"applied: {', '.join(applied)} (embedding dimensions {dimensions})"
        if applied
        else "database is up to date"
    )


if __name__ == "__main__":
    main()
