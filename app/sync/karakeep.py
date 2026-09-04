"""Karakeep access: bookmark creation, payload building, list membership."""

from __future__ import annotations

import contextlib

import requests

from app.config import KARAKEEP_LIST_ID, KARAKEEP_TOKEN, KARAKEEP_URL
from app.sync.enrich import NOTE_MAX, TITLE_MAX, heuristic_enrichment
from app.sync.models import Enrichment, MediaItem

TIMEOUT = 30


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {KARAKEEP_TOKEN}",
        "Content-Type": "application/json",
    }


def build_payload(media: MediaItem, enrichment: Enrichment | None = None) -> dict:
    """Build the bookmark payload; Karakeep cannot crawl Instagram itself.

    ``enrichment`` (typically LLM-generated, see app.sync.enrich) provides the
    title/summary/tags; without it a deterministic heuristic is used.
    """
    e = enrichment or heuristic_enrichment(media)
    return {
        "type": "link",
        "url": f"https://www.instagram.com/p/{media.code}/",
        "title": e.title[:TITLE_MAX],
        "note": e.note[:NOTE_MAX],
        "tags": [t.strip().lower() for t in e.tags if t.strip()][:3],
    }


def _add_to_list(bookmark_id: str) -> None:
    try:
        r = requests.put(
            f"{KARAKEEP_URL}/api/v1/lists/{KARAKEEP_LIST_ID}/bookmarks/{bookmark_id}",
            headers=_headers(),
            timeout=TIMEOUT,
        )
        if r.status_code not in (200, 204):
            raise OSError(f"list add rejected ({r.status_code})")
    except requests.RequestException as exc:
        raise OSError(f"list add failed: {exc}") from exc


def create_bookmark(media: MediaItem, enrichment: Enrichment | None = None) -> bool:
    """Create the bookmark; returns False on failure (caller counts it)."""
    payload = build_payload(media, enrichment=enrichment)
    try:
        r = requests.post(
            f"{KARAKEEP_URL}/api/v1/bookmarks",
            json=payload,
            headers=_headers(),
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        return False

    if r.status_code not in (200, 201):
        return False

    bookmark_id = r.json().get("id")
    if bookmark_id and KARAKEEP_LIST_ID:
        # The bookmark exists; failing the whole push because of the list
        # would duplicate it on retry.
        with contextlib.suppress(OSError):
            _add_to_list(bookmark_id)
    return True
