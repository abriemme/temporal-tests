"""Karakeep access: bookmark creation, payload building, list membership."""

from __future__ import annotations

import contextlib

import requests

from app.config import KARAKEEP_LIST_ID, KARAKEEP_TOKEN, KARAKEEP_URL
from app.sync.enrich import NOTE_MAX, TITLE_MAX
from app.sync.models import EnrichedBookmark, MediaItem

TIMEOUT = 30

# Karakeep lists are stable within a run; fetched once per worker process and
# reused so the classifier sees the same names for every bookmark.
_lists_cache: list[dict] | None = None


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {KARAKEEP_TOKEN}",
        "Content-Type": "application/json",
    }


def fetch_lists(*, force: bool = False) -> list[dict]:
    """Return the account's lists as ``[{"id", "name"}]``, cached per process.

    The classifier needs the exact list names; caching avoids refetching them
    for every bookmark. An unreachable Karakeep yields an empty list (the sync
    then simply creates bookmarks without list routing).
    """
    global _lists_cache
    if _lists_cache is not None and not force:
        return _lists_cache
    try:
        r = requests.get(
            f"{KARAKEEP_URL}/api/v1/lists",
            headers=_headers(),
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
        raw = payload.get("lists", []) if isinstance(payload, dict) else payload
        _lists_cache = [{"id": item["id"], "name": item["name"]} for item in raw]
    except requests.RequestException, ValueError, KeyError, TypeError:
        _lists_cache = []
    return _lists_cache


def build_payload(media: MediaItem, enrichment: EnrichedBookmark) -> dict:
    """Build the bookmark payload; Karakeep cannot crawl Instagram itself.

    ``enrichment`` (LLM-generated, see app.sync.enrich) provides the
    title/summary/tags.
    """
    return {
        "type": "link",
        "url": f"https://www.instagram.com/p/{media.code}/",
        "title": enrichment.title[:TITLE_MAX],
        "note": enrichment.note[:NOTE_MAX],
        "tags": [t.strip().lower() for t in enrichment.tags if t.strip()][:3],
    }


def _add_to_list(bookmark_id: str, list_id: str) -> None:
    try:
        r = requests.put(
            f"{KARAKEEP_URL}/api/v1/lists/{list_id}/bookmarks/{bookmark_id}",
            headers=_headers(),
            timeout=TIMEOUT,
        )
        if r.status_code not in (200, 204):
            raise OSError(f"list add rejected ({r.status_code})")
    except requests.RequestException as exc:
        raise OSError(f"list add failed: {exc}") from exc


def _target_list_ids(chosen_names: list[str]) -> list[str]:
    """Resolve LLM-chosen list names to ids, plus the configured default list.

    Order-preserving and deduplicated: the classified lists come first, then
    ``KARAKEEP_LIST_ID`` (added to every bookmark when set).
    """
    id_by_name = {item["name"]: item["id"] for item in fetch_lists()}
    ids = [id_by_name[name] for name in chosen_names if name in id_by_name]
    if KARAKEEP_LIST_ID:
        ids.append(KARAKEEP_LIST_ID)

    seen: set[str] = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


def create_bookmark(media: MediaItem, enrichment: EnrichedBookmark) -> bool:
    """Create the bookmark and add it to its lists; False on failure."""
    payload = build_payload(media, enrichment)
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
    if bookmark_id:
        # The bookmark exists; failing the whole push because of a list add
        # would duplicate it on retry.
        for list_id in _target_list_ids(enrichment.lists):
            with contextlib.suppress(OSError):
                _add_to_list(bookmark_id, list_id)
    return True
