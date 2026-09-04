"""Deterministic, LLM-free derivation of a bookmark's note and tags.

Pure functions (no I/O, no network): they read the flattened ``MediaItem`` and
produce the rich note that feeds Karakeep's full-text search and the metadata
tags. The push activity merges these tags with the LLM-generated ones.
"""

from __future__ import annotations

import re

from app.config import AUTO_TAGS, HASHTAG_SPAM_THRESHOLD, MAX_TAGS
from app.sync.models import MediaItem

NOTE_MAX = 4000

HASHTAG_RE = re.compile(r"#([^\s#.,;:!?()\[\]{}'\"]{2,50})")
AUTHOR_RE = re.compile(r"^@([A-Za-z0-9._]{1,40})")

# Instagram product_type -> tag.
PRODUCT_TYPE_TAGS = {
    "clips": "reel",
    "igtv": "igtv",
    "feed": "photo",
    "carousel_container": "carousel",
}


def slug_tag(value: str) -> str:
    """Normalize a tag: lowercase, spaces compacted, length bounded."""
    return " ".join(str(value).split()).strip("#@ ").casefold()[:50]


def derive_tags(media: MediaItem) -> list[str]:
    """Deterministic tags from metadata, no LLM nor extra network call."""
    tags: list[str] = []

    def add(value: str | None) -> None:
        if not value:
            return
        tag = slug_tag(value)
        if tag and tag not in tags:
            tags.append(tag)

    if "hashtags" in AUTO_TAGS and media.caption:
        found = HASHTAG_RE.findall(media.caption)
        # An SEO caption (dozens of hashtags) is noise, not a description.
        if len(found) <= HASHTAG_SPAM_THRESHOLD:
            for tag in found:
                add(tag)

    if "author" in AUTO_TAGS:
        add(media.username)

    if "type" in AUTO_TAGS:
        add(PRODUCT_TYPE_TAGS.get(media.product_type or ""))

    if "music" in AUTO_TAGS:
        add(media.music_artist)

    if "location" in AUTO_TAGS:
        add(media.location_city or media.location_name)

    return tags[:MAX_TAGS]


def merge_tags(deterministic: list[str], llm: list[str]) -> list[str]:
    """Union of deterministic and LLM tags, deterministic first, capped."""
    merged: list[str] = []
    for tag in [*deterministic, *(slug_tag(t) for t in llm)]:
        if tag and tag not in merged:
            merged.append(tag)
    return merged[:MAX_TAGS]


def build_note(media: MediaItem) -> str:
    """Rich note: the only text Karakeep's search and LLM tagging will see,
    since the Instagram content is out of reach for crawlers."""
    lines: list[str] = []
    if media.username:
        author = f"@{media.username}"
        if media.full_name:
            author += f" ({media.full_name})"
        lines.append(author)
    if media.music_title:
        song = media.music_title
        if media.music_artist:
            song = f"{media.music_artist} — {song}"
        lines.append(f"Music: {song}")
    place = media.location_name or media.location_city
    if place:
        lines.append(f"Place: {place}")
    if media.duration:
        lines.append(f"Duration: {round(float(media.duration))} s")
    if media.alt_text:
        lines.append(f"Auto description: {media.alt_text}")
    if media.caption:
        lines.append("")
        lines.append(media.caption)
    return "\n".join(lines)[:NOTE_MAX]
