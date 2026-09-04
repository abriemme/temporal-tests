"""Unit tests for the sync service layer (state, karakeep, instagram).

Service functions are tested directly (no worker, no workflow); activity-level
behavior (challenge wrapping, heartbeats, cancellation) is tested through
``ActivityEnvironment``.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import requests
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

import app.sync.instagram as instagram_svc
import app.sync.karakeep as karakeep_svc
import app.sync.state as state_svc
from app.sync.activities import fetch_saved
from app.sync.instagram import is_challenge_error
from app.sync.karakeep import build_payload, create_bookmark, fetch_lists
from app.sync.models import (
    CHALLENGE_ERROR_TYPE,
    EnrichedBookmark,
    MediaItem,
    SyncParams,
)
from app.sync.state import load_seen, save_seen

MEDIA = MediaItem(pk="1", code="abc", username="someone", caption="line1\nline2")
ENRICHMENT = EnrichedBookmark(title="A recipe", note="Pancakes", tags=["food"])


# --- Seen-state ---------------------------------------------------------------


def test_seen_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(state_svc, "STATE_FILE", tmp_path / "seen.json")

    save_seen(["2", "1"])
    assert load_seen() == ["1", "2"]
    assert json.loads((tmp_path / "seen.json").read_text()) == ["1", "2"]


def test_load_seen_missing_or_corrupt_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(state_svc, "STATE_FILE", tmp_path / "seen.json")

    assert load_seen() == []  # no file yet

    (tmp_path / "seen.json").write_text("not json")
    assert load_seen() == []  # unreadable -> start fresh


# --- Karakeep -----------------------------------------------------------------


def test_build_payload_from_enrichment() -> None:
    enrichment = EnrichedBookmark(
        title="A great recipe", note="Pancakes, step by step", tags=["Food", " Baking "]
    )
    payload = build_payload(MEDIA, enrichment)
    assert payload["type"] == "link"
    assert payload["url"] == "https://www.instagram.com/p/abc/"
    assert payload["title"] == "A great recipe"
    assert payload["note"] == "Pancakes, step by step"
    assert payload["tags"] == ["food", "baking"]


def test_fetch_lists_parses_and_caches(monkeypatch) -> None:
    calls = {"n": 0}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "lists": [
                    {"id": "l1", "name": "Cuisine & Vins", "icon": "🍷"},
                    {"id": "l2", "name": "Voyage"},
                ]
            }

    def fake_get(*a, **kw):
        calls["n"] += 1
        return Resp()

    monkeypatch.setattr(karakeep_svc, "_lists_cache", None)
    monkeypatch.setattr("app.sync.karakeep.requests.get", fake_get)

    assert fetch_lists() == [
        {"id": "l1", "name": "Cuisine & Vins"},
        {"id": "l2", "name": "Voyage"},
    ]
    fetch_lists()  # second call served from cache, no extra request
    assert calls["n"] == 1


def test_create_bookmark_unreachable_returns_false(monkeypatch) -> None:
    monkeypatch.setattr(karakeep_svc, "_lists_cache", [])

    def boom(*a, **kw):
        raise requests.RequestException("down")

    monkeypatch.setattr("app.sync.karakeep.requests.post", boom)
    assert create_bookmark(MEDIA, ENRICHMENT) is False


def test_create_bookmark_routes_to_classified_lists(monkeypatch) -> None:
    monkeypatch.setattr(
        karakeep_svc,
        "_lists_cache",
        [{"id": "l1", "name": "Cuisine & Vins"}, {"id": "l2", "name": "Voyage"}],
    )

    class FakeResponse:
        status_code = 201

        def json(self):
            return {"id": "bm-1"}

    put_urls: list[str] = []

    class PutResponse:
        status_code = 204

    def fake_put(url, *a, **kw):
        put_urls.append(url)
        return PutResponse()

    monkeypatch.setattr(
        "app.sync.karakeep.requests.post", lambda *a, **kw: FakeResponse()
    )
    monkeypatch.setattr("app.sync.karakeep.requests.put", fake_put)

    enrichment = EnrichedBookmark(title="t", note="n", tags=[], lists=["Cuisine & Vins"])
    assert create_bookmark(MEDIA, enrichment) is True
    # Only the classified list is targeted (no KARAKEEP_LIST_ID in tests).
    assert len(put_urls) == 1
    assert "/lists/l1/bookmarks/bm-1" in put_urls[0]


# --- Instagram ----------------------------------------------------------------


@pytest.mark.parametrize(
    "message,expected",
    [
        ("ChallengeRequired", True),
        ("login_required: 467", True),
        ("feedback_required", True),
        ("plain network error", False),
    ],
)
def test_is_challenge_error(message: str, expected: bool) -> None:
    assert is_challenge_error(Exception(message)) is expected


@pytest.mark.asyncio
async def test_fetch_saved_wraps_challenge(monkeypatch) -> None:
    def boom(params):
        raise Exception("login_required: checkpoint required")

    monkeypatch.setattr(instagram_svc, "fetch_saved_media", boom)

    env = ActivityEnvironment()
    with pytest.raises(ApplicationError) as exc_info:
        await env.run(fetch_saved, SyncParams())
    assert exc_info.value.type == CHALLENGE_ERROR_TYPE


# --- ActivityEnvironment mechanics (generic) -----------------------------------


@pytest.mark.asyncio
async def test_activity_heartbeats() -> None:
    """``on_heartbeat`` captures the details passed to ``activity.heartbeat()``."""

    @activity.defn
    async def heartbeating_activity(count: int) -> None:
        for i in range(count):
            activity.heartbeat(i)

    heartbeats: list[int] = []
    env = ActivityEnvironment()
    env.on_heartbeat = lambda *args: heartbeats.append(args[0])

    await env.run(heartbeating_activity, 3)

    assert heartbeats == [0, 1, 2]


@pytest.mark.asyncio
async def test_activity_cancellation() -> None:
    """``env.cancel()`` propagates cancellation into the running activity."""

    @activity.defn
    async def cancellable_activity() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(0.01)

    env = ActivityEnvironment()

    async def cancel_soon() -> None:
        await asyncio.sleep(0.05)
        env.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.gather(env.run(cancellable_activity), cancel_soon())
