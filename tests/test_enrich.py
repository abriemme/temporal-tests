"""Tests for the pydantic-ai enrichment of Karakeep bookmarks.

The agent turns a raw Instagram caption into a structured bookmark
(title / summary / tags / lists). Enrichment always runs; a provider error
propagates so the push activity's retry policy can handle it.
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from app.sync.enrich import (
    NOTE_MAX,
    TITLE_MAX,
    EnrichedBookmark,
    enrich_media,
)
from app.sync.models import MediaItem

MEDIA = MediaItem(
    pk="1",
    code="abc",
    username="someone",
    caption="First line of the caption\nMore details here",
)


class _FakeAgent:
    """Agent stub returning a fixed ``EnrichedBookmark`` as its output."""

    def __init__(self, output: EnrichedBookmark) -> None:
        self._output = output

    async def run(self, _prompt: str):
        output = self._output
        return type("Run", (), {"output": output})()


@pytest.mark.asyncio
async def test_enrich_with_test_model_returns_structured_output() -> None:
    agent = Agent(TestModel(), output_type=EnrichedBookmark)
    e = await enrich_media(MEDIA, list_names=["Voyage"], agent=agent)
    # TestModel fills every field of the output schema: the agent path is
    # exercised end-to-end (structured output, not a fallback).
    assert isinstance(e.title, str) and e.title
    assert isinstance(e.note, str)
    assert isinstance(e.tags, list)
    assert isinstance(e.lists, list)


@pytest.mark.asyncio
async def test_enrich_keeps_only_real_list_names() -> None:
    out = EnrichedBookmark(
        title="t",
        note="n",
        tags=["a"],
        lists=["Cuisine & Vins", "Aucune", "Hallucinated"],
    )
    e = await enrich_media(
        MEDIA, list_names=["Cuisine & Vins", "Voyage"], agent=_FakeAgent(out)
    )
    # "Aucune" and any name not matching a real list verbatim are dropped.
    assert e.lists == ["Cuisine & Vins"]


@pytest.mark.asyncio
async def test_enrich_normalizes_and_caps_tags() -> None:
    out = EnrichedBookmark(
        title="t", note="n", tags=[" Food ", "BAKING", "x", "y"], lists=[]
    )
    e = await enrich_media(MEDIA, agent=_FakeAgent(out))
    assert e.tags == ["food", "baking", "x"]


@pytest.mark.asyncio
async def test_enrich_truncates_to_karakeep_limits() -> None:
    out = EnrichedBookmark(title="x" * 300, note="y" * 5000, tags=[], lists=[])
    e = await enrich_media(MEDIA, agent=_FakeAgent(out))
    assert len(e.title) == TITLE_MAX
    assert len(e.note) == NOTE_MAX


@pytest.mark.asyncio
async def test_enrich_agent_failure_propagates() -> None:
    class FailingAgent:
        async def run(self, *a, **kw):
            raise RuntimeError("LLM provider down")

    with pytest.raises(RuntimeError):
        await enrich_media(MEDIA, agent=FailingAgent())  # type: ignore[arg-type]
