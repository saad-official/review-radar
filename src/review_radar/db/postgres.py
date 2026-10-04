"""PostgresStore: the Store protocol over psycopg 3 (sync), a small pool, and pgvector.

Vectors cross the wire as pgvector's text form (`'[0.1,0.2,...]'::vector`) and come back
via `embedding::text`, so no pgvector Python package is needed. Cosine similarity is
`1 - (a <=> b)`; with unit vectors it equals the dot product the MemoryStore computes.

Neon's pooled endpoint is PgBouncer in transaction mode, which breaks server-side prepared
statements: `prepare_threshold=None` turns them off (same as Changelog Forge).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from ..ingest.base import ReviewIn
from ..models import (
    AgentEvent,
    App,
    MemoryHit,
    Proposal,
    Review,
    Run,
    RunStep,
    Signals,
    Theme,
)
from .store import keywords, proposal_text, rrf

S = "review_radar"

_APP_COLUMNS = {"name", "github_repo", "policy", "public", "github_token_ciphertext", "country"}
_THEME_COLUMNS = {
    "title",
    "summary",
    "kind",
    "status",
    "review_count",
    "quotes",
    "embedding",
    "last_run_id",
    "updated_at",
}
_THEME_JSON = {"quotes"}
_RUN_COLUMNS = {
    "status",
    "budget",
    "usage",
    "stats",
    "summary",
    "error",
    "attempts",
    "lease_until",
    "started_at",
    "finished_at",
}
_RUN_JSON = {"budget", "usage", "stats"}
_PROPOSAL_COLUMNS = {
    "status",
    "draft",
    "guardrails",
    "decided_by",
    "decided_at",
    "decision_reason",
    "result",
    "reasoning",
}
_PROPOSAL_JSON = {"draft", "guardrails", "result"}

REVIEW_COLUMNS = (
    "id, app_id, store_review_id, source, author, rating, title, body, app_version, date, "
    "fetched_at"
)
THEME_COLUMNS = (
    "id, app_id, title, summary, kind, status, review_count, quotes, first_run_id, "
    "last_run_id, created_at, updated_at"
)


def vector_literal(vector: list[float] | None) -> str | None:
    if vector is None:
        return None
    return "[" + ",".join(f"{value:.7g}" for value in vector) + "]"


def parse_vector(text: str | None) -> list[float] | None:
    return [float(v) for v in json.loads(text)] if text else None


def _ids(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    row = dict(row)
    for key in keys:
        if row.get(key) is not None:
            row[key] = str(row[key])
    return row


class PostgresStore:
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 4):
        self.pool = ConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={"prepare_threshold": None, "row_factory": dict_row},
        )
        self.pool.open(wait=False)

    def close(self) -> None:
        self.pool.close()

    def _execute(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall()) if cur.description else []

    def _update(
        self,
        table: str,
        key: str,
        allowed: set[str],
        json_columns: set[str],
        ident: str,
        fields: dict[str, Any],
        *,
        where: str = "",
        where_params: tuple[Any, ...] = (),
        returning: str = "",
    ) -> list[dict[str, Any]]:
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"not updatable {table} columns: {sorted(unknown)}")
        if not fields:
            return []
        assignments, values = [], []
        for column, value in fields.items():
            if column == "embedding":
                assignments.append(f"{column} = %s::vector")
                values.append(vector_literal(value))
            else:
                assignments.append(f"{column} = %s")
                values.append(
                    Jsonb(value) if column in json_columns and value is not None else value
                )
        sql = f"UPDATE {S}.{table} SET {', '.join(assignments)} WHERE {key} = %s {where}"
        if returning:
            sql += f" RETURNING {returning}"
        return self._execute(sql, (*values, ident, *where_params))

    def ping(self) -> bool:
        try:
            self._execute("SELECT 1")
            return True
        except Exception:
            return False

    # -- apps -------------------------------------------------------------------------
    def create_app(self, app: App) -> App:
        try:
            self._execute(
                f"""INSERT INTO {S}.apps (id, store, store_id, name, country, github_repo, policy,
                        public, github_token_ciphertext, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    app.id,
                    app.store,
                    app.store_id,
                    app.name,
                    app.country,
                    app.github_repo,
                    app.policy,
                    app.public,
                    app.github_token_ciphertext,
                    app.created_at,
                ),
            )
        except errors.UniqueViolation as exc:
            raise ValueError("app already exists") from exc
        return app

    def get_app(self, app_id: str) -> App | None:
        rows = self._execute(f"SELECT * FROM {S}.apps WHERE id = %s", (app_id,))
        return App(**_ids(rows[0], "id")) if rows else None

    def find_app(self, store: str, store_id: str, country: str) -> App | None:
        rows = self._execute(
            f"SELECT * FROM {S}.apps WHERE store = %s AND store_id = %s AND country = %s",
            (store, store_id, country),
        )
        return App(**_ids(rows[0], "id")) if rows else None

    def list_apps(self) -> list[App]:
        rows = self._execute(f"SELECT * FROM {S}.apps ORDER BY created_at")
        return [App(**_ids(row, "id")) for row in rows]

    def update_app(self, app_id: str, **fields: Any) -> None:
        self._update("apps", "id", _APP_COLUMNS, set(), app_id, fields)

    def delete_app(self, app_id: str) -> bool:
        # Cascades to reviews, themes, runs (and their append-only steps), proposals, events.
        return bool(self._execute(f"DELETE FROM {S}.apps WHERE id = %s RETURNING id", (app_id,)))

    def app_counts(self, app_id: str) -> dict[str, Any]:
        row = self._execute(
            f"""SELECT
                  (SELECT count(*) FROM {S}.reviews WHERE app_id = %(a)s) AS reviews,
                  (SELECT count(*) FROM {S}.signals s JOIN {S}.reviews r ON r.id = s.review_id
                     WHERE r.app_id = %(a)s) AS analysed,
                  (SELECT count(*) FROM {S}.themes WHERE app_id = %(a)s) AS themes""",
            {"a": app_id},
        )[0]
        statuses = self._execute(
            f"SELECT status, count(*) AS n FROM {S}.proposals WHERE app_id = %s GROUP BY status",
            (app_id,),
        )
        return {
            "reviews": int(row["reviews"]),
            "analysed": int(row["analysed"]),
            "themes": int(row["themes"]),
            "proposals": {r["status"]: int(r["n"]) for r in statuses},
        }

    # -- reviews -----------------------------------------------------------------------
    def upsert_reviews(self, app_id: str, reviews: list[ReviewIn]) -> list[str]:
        if not reviews:
            return []
        new_ids: list[str] = []
        with self.pool.connection() as conn, conn.cursor() as cur:
            # executemany(returning=True) pipelines the inserts: one round trip per
            # batch instead of one per review (200 reviews from a distant laptop: ~1 s, not ~200 s).
            cur.executemany(
                f"""INSERT INTO {S}.reviews (id, app_id, store_review_id, source, author,
                        rating, title, body, app_version, date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (app_id, store_review_id) DO NOTHING RETURNING id""",
                [
                    (
                        str(uuid4()),
                        app_id,
                        item.store_review_id,
                        item.source,
                        item.author,
                        item.rating,
                        item.title,
                        item.body,
                        item.app_version,
                        item.date,
                    )
                    for item in reviews
                ],
                returning=True,
            )
            while True:
                row = cur.fetchone()
                if row:
                    new_ids.append(str(row["id"]))
                if not cur.nextset():
                    break
        return new_ids

    def known_review_ids(self, app_id: str) -> set[str]:
        rows = self._execute(
            f"SELECT store_review_id FROM {S}.reviews WHERE app_id = %s", (app_id,)
        )
        return {row["store_review_id"] for row in rows}

    @staticmethod
    def _review(row: dict[str, Any]) -> Review:
        row = _ids(row, "id", "app_id")
        if "embedding" in row:
            row["embedding"] = parse_vector(row["embedding"])
        return Review(**row)

    def get_reviews(self, review_ids: list[str]) -> list[Review]:
        if not review_ids:
            return []
        rows = self._execute(
            f"""SELECT {REVIEW_COLUMNS}, embedding::text AS embedding FROM {S}.reviews
                WHERE id = ANY(%s::uuid[])""",
            (review_ids,),
        )
        by_id = {str(row["id"]): self._review(row) for row in rows}
        return [by_id[r] for r in review_ids if r in by_id]

    def list_reviews(
        self, app_id: str, *, theme_id: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Review]:
        if theme_id is not None:
            rows = self._execute(
                f"""SELECT {", ".join("r." + c.strip() for c in REVIEW_COLUMNS.split(","))}
                    FROM {S}.reviews r JOIN {S}.theme_reviews tr ON tr.review_id = r.id
                    WHERE r.app_id = %s AND tr.theme_id = %s
                    ORDER BY r.date DESC NULLS LAST, r.store_review_id DESC LIMIT %s OFFSET %s""",
                (app_id, theme_id, limit, offset),
            )
        else:
            rows = self._execute(
                f"""SELECT {REVIEW_COLUMNS} FROM {S}.reviews WHERE app_id = %s
                    ORDER BY date DESC NULLS LAST, store_review_id DESC LIMIT %s OFFSET %s""",
                (app_id, limit, offset),
            )
        return [self._review(row) for row in rows]

    def unprocessed_review_ids(self, app_id: str, limit: int) -> list[str]:
        rows = self._execute(
            f"""SELECT r.id FROM {S}.reviews r LEFT JOIN {S}.signals s ON s.review_id = r.id
                WHERE r.app_id = %s AND (s.review_id IS NULL OR
                      (r.embedding IS NULL AND NOT ('prompt_injection' = ANY(s.flags))))
                ORDER BY r.date DESC NULLS LAST, r.store_review_id DESC LIMIT %s""",
            (app_id, limit),
        )
        return [str(row["id"]) for row in rows]

    def set_embeddings(self, vectors: dict[str, list[float]]) -> None:
        if not vectors:
            return
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                f"UPDATE {S}.reviews SET embedding = %s::vector WHERE id = %s",
                [(vector_literal(v), review_id) for review_id, v in vectors.items()],
            )

    def put_signals(self, signals: list[Signals]) -> None:
        if not signals:
            return
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                f"""INSERT INTO {S}.signals (review_id, category, sentiment, severity,
                        feature_area, devices, os_versions, app_versions, quotes, flags, model,
                        prompt_version, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (review_id) DO NOTHING""",
                [
                    (
                        s.review_id,
                        s.category,
                        s.sentiment,
                        s.severity,
                        s.feature_area,
                        s.devices,
                        s.os_versions,
                        s.app_versions,
                        s.quotes,
                        s.flags,
                        s.model,
                        s.prompt_version,
                        s.created_at,
                    )
                    for s in signals
                ],
            )

    def get_signals(self, review_ids: list[str]) -> dict[str, Signals]:
        if not review_ids:
            return {}
        rows = self._execute(
            f"SELECT * FROM {S}.signals WHERE review_id = ANY(%s::uuid[])", (review_ids,)
        )
        return {str(row["review_id"]): Signals(**_ids(row, "review_id")) for row in rows}

    # -- themes -------------------------------------------------------------------------
    @staticmethod
    def _theme(row: dict[str, Any]) -> Theme:
        row = _ids(row, "id", "app_id", "first_run_id", "last_run_id")
        row.pop("tsv", None)
        if "embedding" in row:
            row["embedding"] = parse_vector(row["embedding"])
        return Theme(**row)

    def create_theme(self, theme: Theme) -> Theme:
        self._execute(
            f"""INSERT INTO {S}.themes (id, app_id, title, summary, kind, status, review_count,
                    quotes, embedding, first_run_id, last_run_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s)""",
            (
                theme.id,
                theme.app_id,
                theme.title,
                theme.summary,
                theme.kind,
                theme.status,
                theme.review_count,
                Jsonb([q.model_dump() for q in theme.quotes]),
                vector_literal(theme.embedding),
                theme.first_run_id,
                theme.last_run_id,
                theme.created_at,
                theme.updated_at,
            ),
        )
        return theme

    def get_theme(self, theme_id: str) -> Theme | None:
        rows = self._execute(
            f"SELECT {THEME_COLUMNS}, embedding::text AS embedding FROM {S}.themes WHERE id = %s",
            (theme_id,),
        )
        return self._theme(rows[0]) if rows else None

    def update_theme(self, theme_id: str, **fields: Any) -> None:
        if "quotes" in fields:
            fields["quotes"] = [
                q.model_dump() if hasattr(q, "model_dump") else q for q in fields["quotes"]
            ]
        fields.setdefault("updated_at", datetime.now().astimezone())
        self._update("themes", "id", _THEME_COLUMNS, _THEME_JSON, theme_id, fields)

    def list_themes(self, app_id: str, *, status: str | None = None) -> list[Theme]:
        rows = self._execute(
            f"""SELECT {THEME_COLUMNS} FROM {S}.themes
                WHERE app_id = %s AND (%s::text IS NULL OR status = %s)
                ORDER BY review_count DESC, created_at""",
            (app_id, status, status),
        )
        return [self._theme(row) for row in rows]

    def nearest_themes(
        self, app_id: str, vector: list[float], *, limit: int = 5, kind: str | None = None
    ) -> list[tuple[Theme, float]]:
        rows = self._execute(
            f"""SELECT {THEME_COLUMNS}, embedding::text AS embedding,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM {S}.themes
                WHERE app_id = %s AND embedding IS NOT NULL AND status <> 'ignored'
                  AND (%s::text IS NULL OR kind = %s)
                ORDER BY embedding <=> %s::vector LIMIT %s""",
            (vector_literal(vector), app_id, kind, kind, vector_literal(vector), limit),
        )
        return [
            (
                self._theme({k: v for k, v in row.items() if k != "similarity"}),
                float(row["similarity"]),
            )
            for row in rows
        ]

    def link_reviews(
        self, theme_id: str, links: list[tuple[str, float | None]], run_id: str | None
    ) -> int:
        if not links:
            return 0
        added = 0
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                f"""INSERT INTO {S}.theme_reviews (theme_id, review_id, similarity, run_id)
                    VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING review_id""",
                [(theme_id, review_id, similarity, run_id) for review_id, similarity in links],
                returning=True,
            )
            while True:
                if cur.fetchone():
                    added += 1
                if not cur.nextset():
                    break
        return added

    def theme_review_ids(self, theme_id: str) -> list[str]:
        rows = self._execute(
            f"SELECT review_id FROM {S}.theme_reviews WHERE theme_id = %s", (theme_id,)
        )
        return [str(row["review_id"]) for row in rows]

    def review_theme_ids(self, review_ids: list[str]) -> dict[str, list[str]]:
        if not review_ids:
            return {}
        rows = self._execute(
            f"""SELECT review_id, theme_id FROM {S}.theme_reviews
                WHERE review_id = ANY(%s::uuid[])""",
            (review_ids,),
        )
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(str(row["review_id"]), []).append(str(row["theme_id"]))
        return result

    # -- proposals ------------------------------------------------------------------------
    @staticmethod
    def _proposal(row: dict[str, Any]) -> Proposal:
        return Proposal(**_ids(row, "id", "app_id", "run_id", "theme_id", "review_id"))

    def create_proposal(self, proposal: Proposal) -> Proposal | None:
        rows = self._execute(
            f"""INSERT INTO {S}.proposals (id, app_id, run_id, kind, theme_id, review_id, draft,
                    reasoning, guardrails, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING RETURNING id""",
            (
                proposal.id,
                proposal.app_id,
                proposal.run_id,
                proposal.kind,
                proposal.theme_id,
                proposal.review_id,
                Jsonb(proposal.draft),
                proposal.reasoning,
                Jsonb(proposal.guardrails),
                proposal.status,
                proposal.created_at,
                proposal.updated_at,
            ),
        )
        return proposal if rows else None

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        rows = self._execute(f"SELECT * FROM {S}.proposals WHERE id = %s", (proposal_id,))
        return self._proposal(rows[0]) if rows else None

    def list_proposals(
        self,
        app_id: str,
        *,
        status: str | None = None,
        kind: str | None = None,
        run_id: str | None = None,
    ) -> list[Proposal]:
        rows = self._execute(
            f"""SELECT * FROM {S}.proposals WHERE app_id = %s
                  AND (%s::text IS NULL OR status = %s)
                  AND (%s::text IS NULL OR kind = %s)
                  AND (%s::uuid IS NULL OR run_id = %s::uuid)
                ORDER BY created_at DESC""",
            (app_id, status, status, kind, kind, run_id, run_id),
        )
        return [self._proposal(row) for row in rows]

    def find_proposal(
        self, kind: str, *, review_id: str | None = None, theme_id: str | None = None
    ) -> Proposal | None:
        column, value = ("review_id", review_id) if kind == "reply" else ("theme_id", theme_id)
        rows = self._execute(
            f"SELECT * FROM {S}.proposals WHERE kind = %s AND {column} = %s", (kind, value)
        )
        return self._proposal(rows[0]) if rows else None

    def transition_proposal(
        self, proposal_id: str, from_statuses: tuple[str, ...], **fields: Any
    ) -> Proposal | None:
        # One conditional UPDATE: two concurrent approvals cannot both win, so an issue is
        # created at most once per proposal.
        fields["updated_at"] = datetime.now().astimezone()
        rows = self._update(
            "proposals",
            "id",
            _PROPOSAL_COLUMNS | {"updated_at"},
            _PROPOSAL_JSON,
            proposal_id,
            fields,
            where="AND status = ANY(%s)",
            where_params=(list(from_statuses),),
            returning="*",
        )
        return self._proposal(rows[0]) if rows else None

    # -- runs -----------------------------------------------------------------------------
    @staticmethod
    def _run(row: dict[str, Any]) -> Run:
        return Run(**_ids(row, "id", "app_id"))

    def create_run(self, run: Run) -> Run:
        self._execute(
            f"""INSERT INTO {S}.runs (id, app_id, status, trigger, budget, usage, stats, summary,
                    error, client_key, attempts, lease_until, created_at, started_at, finished_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                run.id,
                run.app_id,
                run.status,
                run.trigger,
                Jsonb(run.budget),
                Jsonb(run.usage) if run.usage is not None else None,
                Jsonb(run.stats),
                run.summary,
                run.error,
                run.client_key,
                run.attempts,
                run.lease_until,
                run.created_at,
                run.started_at,
                run.finished_at,
            ),
        )
        return run

    def get_run(self, run_id: str) -> Run | None:
        rows = self._execute(f"SELECT * FROM {S}.runs WHERE id = %s", (run_id,))
        return self._run(rows[0]) if rows else None

    def update_run(self, run_id: str, **fields: Any) -> None:
        self._update("runs", "id", _RUN_COLUMNS, _RUN_JSON, run_id, fields)

    def claim_run(self, run_id: str, lease_s: float, max_attempts: int) -> Run | None:
        rows = self._execute(
            f"""UPDATE {S}.runs
                SET attempts = attempts + 1, lease_until = now() + make_interval(secs => %s),
                    started_at = COALESCE(started_at, now())
                WHERE id = %s AND status NOT IN ('done', 'failed') AND attempts < %s
                  AND (lease_until IS NULL OR lease_until <= now())
                RETURNING *""",
            (lease_s, run_id, max_attempts),
        )
        return self._run(rows[0]) if rows else None

    def list_runs(self, app_id: str, *, limit: int = 20) -> list[Run]:
        rows = self._execute(
            f"SELECT * FROM {S}.runs WHERE app_id = %s ORDER BY created_at DESC LIMIT %s",
            (app_id, limit),
        )
        return [self._run(row) for row in rows]

    def count_runs_since(self, client_key: str, since: datetime) -> int:
        rows = self._execute(
            f"SELECT count(*) AS n FROM {S}.runs WHERE client_key = %s AND created_at >= %s",
            (client_key, since),
        )
        return int(rows[0]["n"])

    def put_checkpoint(self, run_id: str, key: str, data: Any) -> None:
        self._execute(
            f"""INSERT INTO {S}.run_checkpoints (run_id, key, data) VALUES (%s, %s, %s)
                ON CONFLICT (run_id, key) DO UPDATE SET data = EXCLUDED.data""",
            (run_id, key, Jsonb(data)),
        )

    def get_checkpoints(self, run_id: str) -> dict[str, Any]:
        rows = self._execute(
            f"SELECT key, data FROM {S}.run_checkpoints WHERE run_id = %s", (run_id,)
        )
        return {row["key"]: row["data"] for row in rows}

    def append_step(
        self,
        run_id: str,
        stage: str,
        kind: str,
        name: str,
        args: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> RunStep:
        sql = f"""INSERT INTO {S}.run_steps (run_id, seq, stage, kind, name, args, result, usage)
                  SELECT %s, COALESCE(MAX(seq), 0) + 1, %s, %s, %s, %s, %s, %s
                  FROM {S}.run_steps WHERE run_id = %s
                  RETURNING *"""
        params = (
            run_id,
            stage,
            kind,
            name,
            Jsonb(args or {}),
            Jsonb(result or {}),
            Jsonb(usage) if usage is not None else None,
            run_id,
        )
        try:
            row = self._execute(sql, params)[0]
        except errors.UniqueViolation:
            row = self._execute(sql, params)[0]  # lost a seq race once; the retry wins
        return RunStep(**_ids(row, "run_id"))

    def list_steps(self, run_id: str, after_seq: int = 0) -> list[RunStep]:
        rows = self._execute(
            f"SELECT * FROM {S}.run_steps WHERE run_id = %s AND seq > %s ORDER BY seq",
            (run_id, after_seq),
        )
        return [RunStep(**_ids(row, "run_id")) for row in rows]

    # -- audit and memory -------------------------------------------------------------------
    def append_event(self, event: AgentEvent) -> AgentEvent:
        self._execute(
            f"""INSERT INTO {S}.agent_events (id, app_id, actor, type, entity_type, entity_id,
                    input, output, model, prompt_version, tokens_in, tokens_out, latency_ms,
                    created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                event.id,
                event.app_id,
                event.actor,
                event.type,
                event.entity_type,
                event.entity_id,
                Jsonb(event.input) if event.input is not None else None,
                Jsonb(event.output) if event.output is not None else None,
                event.model,
                event.prompt_version,
                event.tokens_in,
                event.tokens_out,
                event.latency_ms,
                event.created_at,
            ),
        )
        return event

    def list_events(self, app_id: str, *, limit: int = 100) -> list[AgentEvent]:
        rows = self._execute(
            f"""SELECT * FROM {S}.agent_events WHERE app_id = %s
                ORDER BY created_at DESC LIMIT %s""",
            (app_id, limit),
        )
        return [AgentEvent(**_ids(row, "id", "app_id", "entity_id")) for row in rows]

    def search_memory(
        self, app_id: str, query: str, vector: list[float] | None, *, limit: int = 8
    ) -> list[MemoryHit]:
        """Hybrid: Postgres full text (OR of the query's keywords) and pgvector cosine over
        theme centroids, fused with reciprocal rank fusion. Proposals inherit their theme's
        vector rank, so "declined: duplicate of #42" surfaces for a similar new theme."""
        words = keywords(query)[:12]
        tsquery = " | ".join(words) if words else ""
        hits: dict[str, MemoryHit] = {}
        keyword_rank: list[str] = []
        vector_rank: list[str] = []
        if tsquery:
            for row in self._execute(
                f"""SELECT id, title, summary, status,
                           ts_rank(tsv, to_tsquery('english', %s)) AS rank
                    FROM {S}.themes WHERE app_id = %s AND tsv @@ to_tsquery('english', %s)
                    ORDER BY rank DESC LIMIT 20""",
                (tsquery, app_id, tsquery),
            ):
                key = f"theme:{row['id']}"
                keyword_rank.append(key)
                hits[key] = MemoryHit(
                    kind="theme",
                    id=str(row["id"]),
                    title=row["title"],
                    snippet=row["summary"][:300],
                    status=row["status"],
                    score=0.0,
                    theme_id=str(row["id"]),
                )
            for row in self._execute(
                f"""SELECT * FROM (
                        SELECT p.*, ts_rank(to_tsvector('english',
                            coalesce(p.draft->>'title', '') || ' ' ||
                            coalesce(p.draft->>'summary', '') || ' ' ||
                            coalesce(p.draft->>'text', '') || ' ' ||
                            coalesce(p.decision_reason, '')), to_tsquery('english', %s)) AS rank
                        FROM {S}.proposals p WHERE p.app_id = %s) ranked
                    WHERE rank > 0 ORDER BY rank DESC LIMIT 20""",
                (tsquery, app_id),
            ):
                key = f"proposal:{row['id']}"
                keyword_rank.append(key)
                hits[key] = self._proposal_hit({k: v for k, v in row.items() if k != "rank"})
        if vector is not None:
            for row in self._execute(
                f"""SELECT id, title, summary, status, 1 - (embedding <=> %s::vector) AS sim
                    FROM {S}.themes WHERE app_id = %s AND embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector LIMIT 20""",
                (vector_literal(vector), app_id, vector_literal(vector)),
            ):
                if float(row["sim"]) <= 0.3:
                    continue
                key = f"theme:{row['id']}"
                vector_rank.append(key)
                hits.setdefault(
                    key,
                    MemoryHit(
                        kind="theme",
                        id=str(row["id"]),
                        title=row["title"],
                        snippet=row["summary"][:300],
                        status=row["status"],
                        score=0.0,
                        theme_id=str(row["id"]),
                    ),
                )
                for prow in self._execute(
                    f"SELECT * FROM {S}.proposals WHERE theme_id = %s", (row["id"],)
                ):
                    pkey = f"proposal:{prow['id']}"
                    vector_rank.append(pkey)
                    hits.setdefault(pkey, self._proposal_hit(prow))
        fused = rrf([keyword_rank, vector_rank])
        ordered = sorted(fused.items(), key=lambda p: -p[1])[:limit]
        return [hits[key].model_copy(update={"score": round(score, 5)}) for key, score in ordered]

    def _proposal_hit(self, row: dict[str, Any]) -> MemoryHit:
        proposal = self._proposal(row)
        text = proposal_text(proposal)
        return MemoryHit(
            kind="proposal",
            id=proposal.id,
            title=f"{proposal.kind}: {(proposal.draft.get('title') or text)[:120]}",
            snippet=text[:300],
            status=proposal.status,
            score=0.0,
            theme_id=proposal.theme_id,
            decision_reason=proposal.decision_reason,
        )
