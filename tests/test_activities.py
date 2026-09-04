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
import app.sync.state as state_svc
from app.sync.activities import fetch_saved
from app.sync.instagram import is_challenge_error
from app.sync.karakeep import build_payload, create_bookmark
from app.sync.models import CHALLENGE_ERROR_TYPE, MediaItem, SyncParams
from app.sync.state import load_seen, save_seen

MEDIA = MediaItem(pk="1", code="abc", username="someone", caption="line1\nline2")


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


def test_build_payload_injects_title_and_note() -> None:
    payload = build_payload(MEDIA)
    assert payload["type"] == "link"
    assert payload["url"] == "https://www.instagram.com/p/abc/"
    assert payload["title"] == "@someone — line1"
    assert payload["note"] == "line1\nline2"


def test_create_bookmark_unreachable_returns_false(monkeypatch) -> None:
    def boom(*a, **kw):
        raise requests.RequestException("down")

    monkeypatch.setattr("app.sync.karakeep.requests.post", boom)
    assert create_bookmark(MEDIA) is False


def test_create_bookmark_success(monkeypatch) -> None:
    class FakeResponse:
        status_code = 201

        def json(self):
            return {"id": "bm-1"}

    monkeypatch.setattr("app.sync.karakeep.requests.post", lambda *a, **kw: FakeResponse())
    assert create_bookmark(MEDIA) is True


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
