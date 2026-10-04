import json
from pathlib import Path

import httpx
import pytest
import respx

from review_radar.ingest.appstore import (
    FEED_URL,
    AppStoreSource,
    parse_json_page,
    parse_xml_page,
)
from review_radar.ingest.base import IngestError, source_for
from review_radar.ingest.csv import parse_csv

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def url(page: int, app_id: str = "123", fmt: str = "json") -> str:
    return FEED_URL.format(cc="us", page=page, app_id=app_id, fmt=fmt)


def test_parse_json_page_fields():
    reviews = parse_json_page(load("feed_page1.json"))
    assert [r.store_review_id for r in reviews] == ["1001", "1002", "1003"]
    first = reviews[0]
    assert first.rating == 1 and first.title == "Crash on start"
    assert first.body == "Synthetic: the app crashes at start."
    assert first.app_version == "9.1.88" and first.author == "tester1"
    assert first.date is not None and first.date.utcoffset() is not None
    assert first.source == "appstore"


def test_single_entry_object_and_metadata_entry_and_empty_page():
    assert [r.store_review_id for r in parse_json_page(load("feed_single.json"))] == ["1005"]
    # Older feeds put the app's own metadata first: no rating, no content -> skipped.
    assert [r.store_review_id for r in parse_json_page(load("feed_with_meta.json"))] == ["1006"]
    assert parse_json_page(load("feed_empty.json")) == []


def test_parse_xml_reads_text_content_only():
    reviews = parse_xml_page((FIXTURES / "feed.xml").read_text(encoding="utf-8"))
    assert [r.store_review_id for r in reviews] == ["2001", "2002"]
    assert reviews[0].body == "Synthetic: I get logged out after every update & cannot log back in."
    assert reviews[0].rating == 1 and reviews[0].app_version == "9.1.88"
    assert reviews[1].author == "other"


def test_xml_with_dtd_is_refused():
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><feed></feed>'
    with pytest.raises(IngestError) as exc:
        parse_xml_page(evil)
    assert exc.value.code == "bad_feed"


@respx.mock
def test_pagination_stops_at_400_and_dedupes():
    respx.get(url(1)).respond(200, json=load("feed_page1.json"))
    respx.get(url(2)).respond(200, json=load("feed_page2.json"))  # 1003 repeats (page shift)
    respx.get(url(3)).respond(400, text="bad request")
    page4 = respx.get(url(4)).respond(200, json=load("feed_page1.json"))
    result = AppStoreSource().fetch("123", "us")
    assert [r.store_review_id for r in result.reviews] == ["1001", "1002", "1003", "1004"]
    assert result.pages == 2 and result.stopped == "http_400"
    assert not page4.called


@respx.mock
def test_pagination_stops_on_empty_page_and_caps_at_ten():
    respx.get(url(1)).respond(200, json=load("feed_page1.json"))
    respx.get(url(2)).respond(200, json=load("feed_empty.json"))
    result = AppStoreSource().fetch("123", "us")
    assert result.stopped == "empty" and len(result.reviews) == 3

    respx.reset()
    route = respx.get(url__regex=r".*customerreviews/page=\d+/id=9/.*").respond(
        200, json=load("feed_single.json")
    )
    capped = AppStoreSource().fetch("9", "us", max_pages=50)
    assert route.call_count == 10 and capped.pages == 10


@respx.mock
def test_incremental_fetch_stops_at_known_page():
    respx.get(url(1)).respond(200, json=load("feed_page1.json"))
    page2 = respx.get(url(2)).respond(200, json=load("feed_page2.json"))
    result = AppStoreSource().fetch("123", "us", known={"1001", "1002", "1003"})
    assert result.stopped == "known" and not page2.called


@respx.mock
def test_first_page_server_error_is_reported():
    respx.get(url(1)).respond(503)
    with pytest.raises(IngestError) as exc:
        AppStoreSource().fetch("123", "us")
    assert exc.value.code == "feed_error"


@respx.mock
def test_unreachable_feed():
    respx.get(url(1)).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(IngestError) as exc:
        AppStoreSource().fetch("123", "us")
    assert exc.value.code == "feed_unreachable"


def test_bad_store_id_and_google_play_stub():
    with pytest.raises(IngestError):
        AppStoreSource().fetch("com.example.app", "us")
    with pytest.raises(IngestError) as exc:
        source_for("android").fetch("com.example.app", "us")
    assert exc.value.code == "not_supported" and "import" in exc.value.message


def test_csv_basic_schema_and_errors():
    text = (
        "review_id,rating,title,body,author,app_version,date\n"
        "a1,1,Crash,It crashes,ann,2.0,2026-10-01\n"
        "a2,9,Bad rating,Text,bob,2.0,2026-10-01\n"
        "a3,,No rating,Fine,,,\n"
        "a1,1,Duplicate,It crashes again,ann,2.0,2026-10-01\n"
        "a4,2,No body,,x,1,2026-10-01\n"
        "a5,2,Bad date,Text,x,1,yesterday\n"
    )
    result = parse_csv(text)
    assert [r.store_review_id for r in result.reviews] == ["a1", "a3"]
    assert result.reviews[0].rating == 1 and result.reviews[0].source == "csv"
    assert result.reviews[1].rating is None and result.reviews[1].date is None
    assert {e.line for e in result.errors} == {3, 6, 7}
    assert result.rows == 6


def test_csv_play_console_headers_and_derived_ids():
    text = (
        "﻿Package Name,App Version Name,Review Submit Date and Time,Star Rating,"
        "Review Title,Review Text\n"
        "com.x,3.1,2026-10-01T10:00:00Z,2,,Keeps logging me out\n"
        "com.x,3.1,2026-10-01T11:00:00Z,5,,Great\n"
    )
    result = parse_csv(text)
    assert len(result.reviews) == 2 and not result.errors
    first = result.reviews[0]
    assert first.body == "Keeps logging me out" and first.rating == 2
    assert first.app_version == "3.1" and first.date.year == 2026
    assert first.store_review_id.startswith("csv-")
    assert parse_csv(text).reviews[0].store_review_id == first.store_review_id  # stable


def test_csv_requires_body_column():
    with pytest.raises(IngestError) as exc:
        parse_csv("rating,title\n1,x\n")
    assert exc.value.code == "missing_column"
    with pytest.raises(IngestError):
        parse_csv("")
