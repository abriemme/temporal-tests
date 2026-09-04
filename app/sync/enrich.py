"""LLM enrichment of Karakeep bookmarks, powered by pydantic-ai.

An Instagram caption is noisy; the agent turns it into a structured
``Enrichment`` (clean title, short summary, relevant tags) before the bookmark
is created. The enrichment is opt-in (``ENRICH_BOOKMARKS=1`` + API key of the
configured provider) and degrades gracefully: any failure or absence of
configuration falls back to a deterministic heuristic, so the sync itself
never depends on an LLM being reachable.
"""

from __future__ import annotations

import os

from app.sync.models import Enrichment, MediaItem

TITLE_MAX = 250
NOTE_MAX = 4000

__all__ = ["Enrichment", "build_agent", "enrich_media", "heuristic_enrichment"]

_SYSTEM_PROMPT = (
    "You enrich bookmarks for an Instagram post saved to a read-it-later app. "
    "From the author and caption, produce: a concise title (max ~90 chars), "
    "a short note summarizing the content, and 1-3 lowercase tags. "
    "Reply in the language of the caption."
)

# Default model; override with ENRICH_MODEL (e.g. "anthropic:claude-...").
DEFAULT_MODEL = os.environ.get("ENRICH_MODEL", "openai:gpt-4o-mini")


def build_agent():
    """Agent producing a structured ``Enrichment`` for a saved post.

    pydantic-ai is imported lazily: its import chain (beartype...) is
    incompatible with the workflow sandbox's import restrictions.
    """
    from pydantic_ai import Agent

    return Agent(
        DEFAULT_MODEL,
        output_type=Enrichment,
        system_prompt=_SYSTEM_PROMPT,
    )


def _enrichment_enabled() -> bool:
    if os.environ.get("ENRICH_BOOKMARKS", "0") != "1":
        return False
    # The provider key depends on the model prefix; OpenAI is the default.
    provider = DEFAULT_MODEL.split(":", 1)[0]
    key_var = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(
        provider, f"{provider.upper()}_API_KEY"
    )
    return bool(os.environ.get(key_var))


def heuristic_enrichment(media: MediaItem) -> Enrichment:
    """Deterministic fallback: first caption line as title, caption as note."""
    title = f"@{media.username}"
    first_line = media.caption.splitlines()[0] if media.caption else ""
    if first_line:
        title += f" — {first_line[:90]}"
    return Enrichment(title=title[:TITLE_MAX], note=media.caption[:NOTE_MAX], tags=[])


async def enrich_media(media: MediaItem, agent=None) -> Enrichment:
    """Return the enrichment for ``media``, never raising.

    Falls back to the heuristic when the feature is disabled, unconfigured,
    or when the agent fails for any reason.
    """
    if _enrichment_enabled():
        try:
            run = await (agent or build_agent()).run(
                f"Author: @{media.username}\nCaption:\n{media.caption}"
            )
            out = run.output
            return Enrichment(
                title=out.title[:TITLE_MAX],
                note=out.note[:NOTE_MAX],
                tags=[t.strip().lower() for t in out.tags if t.strip()][:3],
            )
        except Exception:  # noqa: BLE001 - enrichment must never break the sync
            pass
    return heuristic_enrichment(media)
