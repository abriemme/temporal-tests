"""Activity unit tests using ``ActivityEnvironment``.

``ActivityEnvironment`` runs an activity function directly, outside of any
worker or workflow, while still providing a valid ``activity`` context (logger,
``activity.info()``, heartbeats, cancellation). This is the fast, isolated way
to test the business logic of an activity.

See https://docs.temporal.io/develop/python/best-practices/testing-suite.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import requests
from temporalio import activity
from temporalio.testing import ActivityEnvironment

import app.sync_activities as sync_activities
from app.sync_activities import (
    MediaItem,
    _build_payload,
    load_seen,
    push_to_karakeep,
    save_seen,
)


@pytest.mark.asyncio
async def test_seen_roundtrip(tmp_path, monkeypatch) -> None:
    """save_seen then load_seen returns the persisted set, sorted."""
    monkeypatch.setattr(sync_activities, "STATE_FILE", tmp_path / "seen.json")

    env = ActivityEnvironment()
    await env.run(save_seen, ["2", "1"])
    assert await env.run(load_seen) == ["1", "2"]
    assert json.loads((tmp_path / "seen.json").read_text()) == ["1", "2"]


@pytest.mark.asyncio
async def test_load_seen_missing_or_corrupt_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sync_activities, "STATE_FILE", tmp_path / "seen.json")
    env = ActivityEnvironment()

    assert await env.run(load_seen) == []  # no file yet

    (tmp_path / "seen.json").write_text("not json")
    assert await env.run(load_seen) == []  # unreadable -> start fresh


@pytest.mark.asyncio
async def test_push_to_karakeep_unreachable_returns_false(monkeypatch) -> None:
    monkeypatch.setattr(sync_activities, "KARAKEEP_URL", "http://localhost:1")

    def boom(*a, **kw):
        raise requests.RequestException("down")

    monkeypatch.setattr(sync_activities.requests, "post", boom)

    env = ActivityEnvironment()
    media = MediaItem(pk="1", code="c1", username="a", caption="cap")
    assert await env.run(push_to_karakeep, media) is False


def test_build_payload_injects_title_and_note() -> None:
    media = MediaItem(pk="1", code="abc", username="someone", caption="line1\nline2")
    payload = _build_payload(media)
    assert payload["type"] == "link"
    assert payload["url"] == "https://www.instagram.com/p/abc/"
    assert payload["title"] == "@someone — line1"
    assert payload["note"] == "line1\nline2"


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
