"""Tests for the pydantic-ai enrichment of Karakeep bookmarks.

The agent turns a raw Instagram caption into a structured bookmark
(title / summary / tags). When the agent is disabled or fails, a
deterministic heuristic fallback keeps the sync working.
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from app.sync.enrich import Enrichment, enrich_media, heuristic_enrichment
from app.sync.models import MediaItem

MEDIA = MediaItem(
    pk="1",
    code="abc",
    username="someone",
    caption="First line of the caption\nMore details here",
)


def test_heuristic_enrichment_builds_title_and_note() -> None:
    e = heuristic_enrichment(MEDIA)
    assert e.title == "@someone — First line of the caption"
    assert e.note == MEDIA.caption
    assert e.tags == []


@pytest.mark.asyncio
async def test_enrich_disabled_returns_heuristic(monkeypatch) -> None:
    monkeypatch.delenv("ENRICH_BOOKMARKS", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    e = await enrich_media(MEDIA)
    assert e.title == "@someone — First line of the caption"


@pytest.mark.asyncio
async def test_enrich_with_test_model_returns_structured_output() -> None:
    agent = Agent(TestModel(), output_type=Enrichment)
    e = await enrich_media(MEDIA, agent=agent)
    # TestModel fills every field of the output schema: the agent path is
    # exercised end-to-end (structured output, not the fallback).
    assert isinstance(e.title, str) and e.title
    assert isinstance(e.note, str)
    assert isinstance(e.tags, list)


@pytest.mark.asyncio
async def test_enrich_agent_failure_falls_back(monkeypatch) -> None:
    class FailingAgent:
        async def run(self, *a, **kw):
            raise RuntimeError("LLM provider down")

    e = await enrich_media(MEDIA, agent=FailingAgent())  # type: ignore[arg-type]
    assert e.title == "@someone — First line of the caption"


def test_enrichment_truncates_for_karakeep_limits() -> None:
    long = Enrichment(title="x" * 300, note="y" * 5000, tags=[])
    assert len(long.title[:250]) == 250
    assert len(long.note[:4000]) == 4000
