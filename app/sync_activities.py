"""Sync activities: the only place doing I/O (Instagram API, Karakeep API, seen-state).

The workflow (app.sync_workflow) stays pure orchestration; these activities are
what the n8n script did inline, now individually retryable and testable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import requests
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.config import (
    DATA_DIR,
    KARAKEEP_LIST_ID,
    KARAKEEP_TOKEN,
    KARAKEEP_URL,
    MAX_ITEMS,
    STATE_FILE,
    get_instagram_client,
)

# Markers that an Instagram error is actually a challenge/checkpoint/rate
# limit: the right reaction is to stop and cool down, not to retry.
CHALLENGE_MARKERS = ("challenge", "checkpoint", "467", "feedback_required")

CHALLENGE_ERROR_TYPE = "ChallengeDetected"


@dataclass
class SyncParams:
    backfill: bool = False
    max_items: int = MAX_ITEMS


@dataclass
class MediaItem:
    pk: str
    code: str
    username: str
    caption: str


def _raise_if_challenge(exc: Exception) -> None:
    if any(marker in str(exc).lower() for marker in CHALLENGE_MARKERS):
        raise ApplicationError(str(exc), type=CHALLENGE_ERROR_TYPE) from exc


# --- Instagram ---------------------------------------------------------------


@activity.defn
async def fetch_saved(params: SyncParams) -> list[MediaItem]:
    """Fetch the saved-posts collection, newest first."""
    cl = get_instagram_client()
    try:
        amount = 0 if params.backfill else params.max_items  # 0 = all
        medias = cl.collection_medias("ALL_MEDIA_AUTO_COLLECTION", amount=amount)
    except Exception as exc:  # instagrapi exceptions are not typed in the sandbox
        _raise_if_challenge(exc)
        raise
    return [
        MediaItem(
            pk=str(m.pk),
            code=m.code,
            username=getattr(m.user, "username", "?") or "?",
            caption=(m.caption_text or "").strip(),
        )
        for m in medias
    ]


# --- Karakeep ----------------------------------------------------------------


def _karakeep_headers() -> dict:
    return {
        "Authorization": f"Bearer {KARAKEEP_TOKEN}",
        "Content-Type": "application/json",
    }


def _build_payload(media: MediaItem) -> dict:
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
    if not KARAKEEP_LIST_ID:
        return
    try:
        r = requests.put(
            f"{KARAKEEP_URL}/api/v1/lists/{KARAKEEP_LIST_ID}/bookmarks/{bookmark_id}",
            headers=_karakeep_headers(),
            timeout=30,
        )
        if r.status_code not in (200, 204):
            activity.logger.warning("List add rejected (%s)", r.status_code)
    except requests.RequestException as exc:
        activity.logger.warning("List add failed: %s", exc)


@activity.defn
async def push_to_karakeep(media: MediaItem) -> bool:
    """Create the bookmark (and add it to the list if configured)."""
    payload = _build_payload(media)
    try:
        r = requests.post(
            f"{KARAKEEP_URL}/api/v1/bookmarks",
            json=payload,
            headers=_karakeep_headers(),
            timeout=30,
        )
    except requests.RequestException as exc:
        activity.logger.error("Karakeep unreachable: %s", exc)
        return False

    if r.status_code not in (200, 201):
        activity.logger.error("Karakeep %s: %s", r.status_code, r.text[:300])
        return False

    activity.logger.info("+ %s", payload["url"])
    bookmark_id = r.json().get("id")
    if bookmark_id:
        _add_to_list(bookmark_id)
    return True


# --- Seen-state (deduplication) ----------------------------------------------


@activity.defn
async def load_seen() -> list[str]:
    if not STATE_FILE.exists():
        return []
    try:
        return sorted(json.loads(STATE_FILE.read_text()))
    except json.JSONDecodeError:
        activity.logger.warning("seen.json unreadable, starting fresh")
        return []


@activity.defn
async def save_seen(seen: list[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(seen), indent=0))
