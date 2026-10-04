"""A keyword baseline for extraction: what you get with no model at all.

It exists so the model's extraction accuracy means something: "86% category accuracy" is
only impressive next to what twenty lines of keywords achieve on the same 200 reviews.
"""

from __future__ import annotations

import re

DEVICE = re.compile(
    r"\b(?:iphone|ipad|ipod|apple\s*watch|airpods|homepod|carplay|mac(?:book)?|playstation|"
    r"xbox|android|pixel|galaxy|ios\s*\d+(?:\.\d+)*)\b",
    re.IGNORECASE,
)
RULES = [
    (
        "bug",
        r"crash|bug|glitch|not work|doesn.?t work|won.?t (?:work|play|open)|stopp?ed working|"
        r"closes|freez|broken|error|can.?t (?:log|sign|play)|fix",
    ),
    ("performance", r"\bslow\b|\blag|battery|loading"),
    (
        "billing",
        r"\bads?\b|\badds\b|advert|commercial|premium|subscri|price|pay|charg|money|"
        r"anuncio|publicidad|cheaper|skips?\b",
    ),
    (
        "request",
        r"please add|add (?:a|more|some)|wish|would like|bring back|should (?:be|add)|"
        r"remove|let me|need to",
    ),
    (
        "praise",
        r"love|best|great|good|awesome|amazing|perfect|goat|excellent|favorite|10/10|"
        r"\bw\b|recommend",
    ),
]
NEGATIVE = r"hate|worst|bad|terrible|annoying|suck|ridiculous|horrible|angry|too many|insane|why"
POSITIVE = r"love|best|great|good|awesome|amazing|perfect|excellent|favorite|recommend|cool"


def predict(title: str, body: str, rating: int | None) -> dict[str, object]:
    text = f"{title} {body}".lower()
    category = next((name for name, pattern in RULES if re.search(pattern, text)), "other")
    positive = bool(re.search(POSITIVE, text)) or (rating or 0) >= 4
    negative = bool(re.search(NEGATIVE, text)) or (rating or 5) <= 2
    if positive and negative:
        sentiment = "mixed"
    elif negative:
        sentiment = "negative"
    elif positive:
        sentiment = "positive"
    else:
        sentiment = "neutral"
    return {
        "category": category,
        "sentiment": sentiment,
        "has_device_info": bool(DEVICE.search(text)),
    }
