"""Optional Langfuse export of a run's call records, plus structured logging setup.

llm-kit has no tracing hook, and adding the Langfuse SDK for one batch POST per run would
be a large dependency for a small job. Instead, after a run finishes, its Ledger records
(model, tokens, cost, latency, errors - never prompt text) are sent to Langfuse's public
ingestion API as one trace with one generation per call. Off unless LANGFUSE_PUBLIC_KEY and
LANGFUSE_SECRET_KEY are set; failures are logged and swallowed.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
from llm_kit import Ledger

from .config import AppSettings
from .models import utcnow

log = logging.getLogger(__name__)


def langfuse_batch(run_id: str, ledger: Ledger, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    now = utcnow().isoformat()
    batch: list[dict[str, Any]] = [
        {
            "id": str(uuid4()),
            "timestamp": now,
            "type": "trace-create",
            "body": {"id": run_id, "name": "review-radar.run", "metadata": metadata},
        }
    ]
    for record in ledger.records:
        start = record.started_at or now
        try:
            end = (datetime.fromisoformat(start) + timedelta(seconds=record.latency_s)).isoformat()
        except ValueError:
            end = start
        batch.append(
            {
                "id": str(uuid4()),
                "timestamp": now,
                "type": "generation-create",
                "body": {
                    "id": str(uuid4()),
                    "traceId": run_id,
                    "name": record.label,
                    "model": record.model,
                    "startTime": start,
                    "endTime": end,
                    "usageDetails": {
                        "input": record.usage.prompt_tokens,
                        "output": record.usage.completion_tokens,
                    },
                    "costDetails": {"total": record.cost_usd},
                    "metadata": {
                        "provider": record.provider,
                        "reasoning_tokens": record.usage.reasoning_tokens,
                        "attempts": record.attempts,
                        "finish_reason": record.finish_reason,
                    },
                    "level": "ERROR" if record.error else "DEFAULT",
                    "statusMessage": record.error,
                },
            }
        )
    return batch


def export_run(
    settings: AppSettings,
    run_id: str,
    ledger: Ledger,
    metadata: dict[str, Any],
    *,
    client: httpx.Client | None = None,
) -> bool:
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return False
    auth = (
        settings.langfuse_public_key.get_secret_value(),
        settings.langfuse_secret_key.get_secret_value(),
    )
    http = client or httpx.Client(timeout=10.0)
    try:
        response = http.post(
            f"{settings.langfuse_host.rstrip('/')}/api/public/ingestion",
            json={"batch": langfuse_batch(run_id, ledger, metadata)},
            auth=auth,
        )
        if response.status_code >= 300:
            log.warning("langfuse ingestion returned %s", response.status_code)
            return False
        return True
    except httpx.HTTPError as exc:
        log.warning("langfuse ingestion failed: %s", exc)
        return False
    finally:
        if client is None:
            http.close()


class JsonFormatter(logging.Formatter):
    """One JSON object per line: what Vercel/Render log search can actually filter on."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "at": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if any(getattr(h, "_review_radar", False) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._review_radar = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
