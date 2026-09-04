"""Karakeep access: bookmark creation, payload building, list membership."""

from __future__ import annotations

import requests

from app.config import KARAKEEP_LIST_ID, KARAKEEP_TOKEN, KARAKEEP_URL
from app.sync.models import MediaItem

TIMEOUT = 30


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {KARAKEEP_TOKEN}",
        "Content-Type": "application/json",
    }


def build_payload(media: MediaItem) -> dict:
    """Karakeep cannot crawl Instagram: we provide title and note ourselves."""
    url = f"https://www.instagram.com/p/{media.code}/"
    title = f"@{media.username}"
    first_line = media.caption.splitlines()[0] if media.caption else ""
    if first_line:
        title += f" — {first_line[:90]}"

    payload = {"type": "link", "url": url, "title": title[:250]}
    if media.caption:
        payload["note"] = media.caption[:4000]
    return payload


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


def create_bookmark(media: MediaItem) -> bool:
    """Create the bookmark; returns False on failure (caller counts it)."""
    payload = build_payload(media)
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
        try:
            _add_to_list(bookmark_id)
        except OSError:
            # The bookmark exists; failing the whole push because of the list
            # would duplicate it on retry.
            pass
    return True
