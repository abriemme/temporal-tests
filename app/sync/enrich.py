"""LLM enrichment of Karakeep bookmarks, powered by pydantic-ai.

An Instagram caption is noisy; the agent turns it into a structured
``EnrichedBookmark`` before the bookmark is created:

- a clean title and a short note summarizing the content;
- 1-3 tags (reusing the caption's hashtags when it has any, otherwise
  generated);
- the Karakeep list(s) the post belongs to, chosen from the account's actual
  lists and copied verbatim (accents and ampersands included).

The enrichment always runs (GPT-5-mini by default): the push activity's
``RetryPolicy`` absorbs transient provider errors, so a temporary LLM outage
delays a bookmark rather than corrupting it.
"""

from __future__ import annotations

import os

from app.sync.models import EnrichedBookmark, MediaItem

TITLE_MAX = 250
NOTE_MAX = 4000

# Sentinel the model returns when no list fits; must never become a real tag.
NO_LIST = "Aucune"

__all__ = ["EnrichedBookmark", "build_agent", "enrich_media"]

_SYSTEM_PROMPT = (
    "You enrich bookmarks for Instagram posts saved to a read-it-later app. "
    "From the author, the caption and the account's Karakeep lists, produce:\n"
    "- title: concise, max ~90 characters;\n"
    "- note: a short summary of the content;\n"
    "- tags: 1-3 lowercase tags. If the caption already contains hashtags, "
    "reuse the most relevant ones (without the '#'); otherwise generate them.\n"
    "- lists: the name(s) of the list(s) this post clearly belongs to. Copy "
    "each name exactly as given, accents and ampersands included. You may pick "
    "several. If no list clearly fits, return an empty list — never force a "
    f'classification (do not invent a "{NO_LIST}" list).\n'
    "Reply in the language of the caption."
)

# Default model; override with ENRICH_MODEL (e.g. "anthropic:claude-...").
DEFAULT_MODEL = os.environ.get("ENRICH_MODEL", "openai:gpt-5-mini")


def build_agent():
    """Agent producing a structured ``EnrichedBookmark`` for a saved post.

    pydantic-ai is imported lazily: its import chain (beartype...) is
    incompatible with the workflow sandbox's import restrictions.
    """
    from pydantic_ai import Agent

    return Agent(
        DEFAULT_MODEL,
        output_type=EnrichedBookmark,
        system_prompt=_SYSTEM_PROMPT,
    )


def _prompt(media: MediaItem, list_names: list[str]) -> str:
    lists_block = "\n".join(f"- {name}" for name in list_names) or "(none)"
    return (
        f"Author: @{media.username}\n"
        f"Caption:\n{media.caption}\n\n"
        f"Available Karakeep lists:\n{lists_block}"
    )


async def enrich_media(
    media: MediaItem,
    list_names: list[str] | None = None,
    agent=None,
) -> EnrichedBookmark:
    """Return the structured bookmark for ``media`` (title, note, tags, lists).

    ``list_names`` are the account's actual Karakeep lists offered to the model
    for classification. Raises on LLM failure; the caller's activity retry
    policy decides whether to retry.
    """
    available = list_names or []
    run = await (agent or build_agent()).run(_prompt(media, available))
    out = run.output

    # Trust only names that match a real list verbatim: this drops the
    # "Aucune" sentinel and any hallucinated name.
    allowed = set(available)
    chosen = [name for name in out.lists if name in allowed and name != NO_LIST]

    return EnrichedBookmark(
        title=out.title[:TITLE_MAX],
        note=out.note[:NOTE_MAX],
        tags=[t.strip().lower() for t in out.tags if t.strip()][:3],
        lists=chosen,
    )
