"""Text stays UTF-8 end to end: the fixture, the App Store feed, CSV uploads. Regression for
"I’m angry" being stored as "Iâ€™m angry" (UTF-8 bytes decoded as cp1252 on Windows)."""

import ast
import json
from pathlib import Path

import pytest
import respx

from review_radar.demo import FIXTURE, fixture_reviews
from review_radar.ingest.appstore import FEED_URL, AppStoreSource, parse_xml_page
from review_radar.ingest.csv import parse_csv
from review_radar.text import decode_upload, fix_mojibake

from .conftest import make_engine
from .test_api import OP, client_for, make_app

ROOT = Path(__file__).resolve().parents[1]
APOS = "’"  # ’
MOJIBAKE = APOS.encode("utf-8").decode("cp1252")  # "â€™"


def test_eval_fixture_keeps_curly_apostrophes():
    raw = json.loads(FIXTURE.read_bytes().decode("utf-8"))["reviews"]
    expected = [r for r in raw if APOS in r["title"] + r["body"]]
    assert expected, "the fixture should contain at least one ’"
    loaded = {r.store_review_id: r for r in fixture_reviews(len(raw))}
    for item in expected:
        review = loaded[item["store_review_id"]]
        assert (review.title, review.body) == (item["title"], item["body"])
    assert not any(MOJIBAKE in r.title + r.body for r in loaded.values())


FEED_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:im="http://itunes.apple.com/rss">'
    "<entry><id>3001</id><title>I’m angry</title><author><name>a</name></author>"
    '<content type="text">It’s broken – again</content><im:rating>1</im:rating>'
    "<im:version>9.1</im:version><updated>2026-10-04T10:00:00-07:00</updated></entry>"
    "</feed>"
).encode()


@respx.mock
def test_xml_feed_ignores_a_wrong_charset_header():
    url = FEED_URL.format(cc="us", page=1, app_id="5", fmt="xml")
    respx.get(url).respond(
        200, content=FEED_XML, headers={"Content-Type": "text/xml; charset=ISO-8859-1"}
    )
    respx.get(FEED_URL.format(cc="us", page=2, app_id="5", fmt="xml")).respond(400)
    [review] = AppStoreSource(fmt="xml").fetch("5", "us").reviews
    assert review.title == "I’m angry" and review.body == "It’s broken – again"
    assert parse_xml_page(FEED_XML)[0].title == "I’m angry"


@respx.mock
def test_json_feed_ignores_a_wrong_charset_header():
    entry = {
        "id": {"label": "3002"},
        "author": {"name": {"label": "a"}},
        "im:rating": {"label": "2"},
        "title": {"label": "I’m angry"},
        "content": {"label": "Don’t update"},
        "im:version": {"label": "9.1"},
        "updated": {"label": "2026-10-04T10:00:00-07:00"},
    }
    body = json.dumps({"feed": {"entry": [entry]}}, ensure_ascii=False).encode("utf-8")
    respx.get(FEED_URL.format(cc="us", page=1, app_id="6", fmt="json")).respond(
        200, content=body, headers={"Content-Type": "application/json; charset=windows-1252"}
    )
    respx.get(FEED_URL.format(cc="us", page=2, app_id="6", fmt="json")).respond(400)
    [review] = AppStoreSource().fetch("6", "us").reviews
    assert review.title == "I’m angry" and review.body == "Don’t update"


CSV_TEXT = "store_review_id,rating,title,body,date\nc1,1,I’m angry,It’s broken,2026-10-01\n"


@pytest.mark.parametrize(
    "raw",
    [
        CSV_TEXT.encode("utf-8"),
        CSV_TEXT.encode("utf-8-sig"),
        CSV_TEXT.encode("cp1252"),  # Excel's legacy "CSV" save on Windows
    ],
    ids=["utf8", "utf8-bom", "cp1252"],
)
def test_csv_bytes_decode_without_mojibake(raw):
    [review] = parse_csv(raw).reviews
    assert review.store_review_id == "c1"
    assert review.title == "I’m angry" and review.body == "It’s broken"


@pytest.mark.anyio
async def test_csv_upload_route_keeps_utf8(settings):
    engine = make_engine(settings)
    async with client_for(engine) as client:
        app = await make_app(client)
        files = {"file": ("reviews.csv", CSV_TEXT.encode("utf-8"), "text/csv")}
        result = await client.post(f"/api/apps/{app['id']}/import", files=files, headers=OP)
        assert result.json()["imported"] == 1
        [review] = (await client.get(f"/api/apps/{app['id']}/reviews", headers=OP)).json()
    assert review["title"] == "I’m angry" and review["body"] == "It’s broken"


def test_decode_upload():
    assert decode_upload("I’m".encode()) == "I’m"
    assert decode_upload("﻿I’m".encode()) == "I’m"
    assert decode_upload("I’m".encode("cp1252")) == "I’m"


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("I" + MOJIBAKE + "m angry", "I’m angry"),
        ("cafÃ© â€“ ok", "café – ok"),  # "cafÃ© â€“ ok"
        (("I’m".encode().decode("cp1252")).encode().decode("cp1252"), "I’m"),  # twice
    ],
)
def test_fix_mojibake_repairs(stored, expected):
    assert fix_mojibake(stored) == expected


@pytest.mark.parametrize(
    "good",
    ["", "plain ascii", "I’m angry", "café naïve", "emoji 😀 and ’", "50€ – 2™", "Ã alone"],
)
def test_fix_mojibake_leaves_correct_text_alone(good):
    assert fix_mojibake(good) == good


def _unspecified_encoding(tree: ast.AST) -> list[int]:
    """Lines that read or write text with the platform default encoding."""
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        keywords = {k.arg for k in node.keywords}
        if "encoding" in keywords:
            continue
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        # read_text(encoding) / write_text(data, encoding) may pass it positionally.
        missing = {"read_text": 1, "write_text": 2}.get(name)
        if missing is not None and len(node.args) < missing:
            lines.append(node.lineno)
        elif name == "open" and isinstance(func, ast.Name):
            mode = node.args[1] if len(node.args) > 1 else None
            binary = isinstance(mode, ast.Constant) and "b" in str(mode.value)
            if not binary:
                lines.append(node.lineno)
    return lines


def test_no_text_io_uses_the_platform_default_encoding():
    offenders = {}
    for folder in ("src", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            found = _unspecified_encoding(ast.parse(path.read_text(encoding="utf-8")))
            if found:
                offenders[str(path.relative_to(ROOT))] = found
    assert offenders == {}
