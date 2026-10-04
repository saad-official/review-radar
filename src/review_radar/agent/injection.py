"""A deterministic prompt-injection detector for review text.

This is one layer of four (docs/security.md), and deliberately the dumbest one. It cannot
catch every injection, and it does not have to: a review it misses is still wrapped in
tags, still treated as data by the prompts, and still cannot make anything happen, because
the agent's tools only create proposals and the evidence rules below refuse flagged
reviews. What the detector buys is that the *obvious* attempts are quarantined before any
model reads them as evidence, and that the quarantine is visible (`signals.flags`).
"""

from __future__ import annotations

import re

PATTERNS = [
    r"\bignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier|your|these)\s+"
    r"(?:instructions?|prompts?|rules?|directions?)",
    r"\bdisregard\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|your)\b",
    r"\b(?:system|developer)\s+prompt\b",
    r"\byou\s+are\s+now\s+(?:a|an|the|in)\b",
    r"\b(?:open|create|file|submit)\s+(?:a|an|the)?\s*(?:new\s+)?(?:github\s+)?issue\s+"
    r"(?:titled|called|named|with\s+(?:the\s+)?title)\b",
    r"\bnew\s+instructions?\s*:",
    r"</?\s*(?:review|reviews|theme|memory|system|assistant|tool)\s*>",
    r"\b(?:as\s+an?\s+ai|language\s+model)\b.{0,40}\b(?:must|should|will)\b",
    r"\bjailbreak\b|\bDAN\s+mode\b",
]
_COMPILED = re.compile("|".join(f"(?:{p})" for p in PATTERNS), re.IGNORECASE | re.DOTALL)

FLAG = "prompt_injection"


def detect_injection(text: str) -> bool:
    return bool(_COMPILED.search(text or ""))
