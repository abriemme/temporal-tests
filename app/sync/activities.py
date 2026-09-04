"""Sync activities: thin Temporal wrappers around the service layer.

All I/O lives in app.sync.instagram / karakeep / state; these decorators only
add the activity contract (logging, challenge detection, error typing).
"""

from __future__ import annotations

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.sync import instagram, karakeep, state
from app.sync.enrich import enrich_media
from app.sync.instagram import is_challenge_error
from app.sync.models import CHALLENGE_ERROR_TYPE, MediaItem, SyncParams


@activity.defn
async def fetch_saved(params: SyncParams) -> list[MediaItem]:
    """Fetch the saved-posts collection, newest first."""
    try:
        return instagram.fetch_saved_media(params)
    except Exception as exc:
        # Never retry an Instagram challenge automatically: hammering a
        # limited account only makes the block worse.
        if is_challenge_error(exc):
            raise ApplicationError(str(exc), type=CHALLENGE_ERROR_TYPE) from exc
        raise


@activity.defn
async def push_to_karakeep(media: MediaItem) -> bool:
    """Enrich the post, create the bookmark and route it to its list(s)."""
    # The classifier picks from the account's real lists (cached per worker).
    lists = karakeep.fetch_lists()
    enrichment = await enrich_media(media, list_names=[item["name"] for item in lists])
    pushed = karakeep.create_bookmark(media, enrichment)
    if pushed:
        activity.logger.info("+ https://www.instagram.com/p/%s/", media.code)
    else:
        activity.logger.error("Karakeep push failed for pk=%s", media.pk)
    return pushed


@activity.defn
async def load_seen() -> list[str]:
    seen = state.load_seen()
    if not seen:
        activity.logger.info("Starting with an empty seen-state.")
    return seen


@activity.defn
async def save_seen(seen: list[str]) -> None:
    state.save_seen(seen)
