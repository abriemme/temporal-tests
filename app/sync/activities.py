"""Sync activities: thin Temporal wrappers around the service layer.

All I/O lives in app.sync.instagram / karakeep / state; these decorators add
the activity contract (logging, challenge detection, error typing) and the
per-post enrichment/tagging orchestration.
"""

from __future__ import annotations

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.sync import instagram, karakeep, state
from app.sync.enrich import enrich_media
from app.sync.facts import build_note, derive_tags, merge_tags
from app.sync.instagram import is_challenge_error
from app.sync.models import (
    CHALLENGE_ERROR_TYPE,
    EnrichedBookmark,
    FetchPageParams,
    FetchPageResult,
    PushOutcome,
    PushParams,
)


@activity.defn
async def fetch_saved_page(params: FetchPageParams) -> FetchPageResult:
    """Fetch one page of the saved-posts collection (newest first)."""
    try:
        return instagram.fetch_saved_page(
            cursor=params.cursor,
            backfill=params.backfill,
            max_items=params.max_items,
        )
    except Exception as exc:
        # Never retry an Instagram challenge automatically: hammering a
        # limited account only makes the block worse.
        if is_challenge_error(exc):
            raise ApplicationError(str(exc), type=CHALLENGE_ERROR_TYPE) from exc
        raise


@activity.defn
async def push_to_karakeep(params: PushParams) -> PushOutcome:
    """Create the bookmark (or complete an existing one in reconcile mode).

    A new bookmark is enriched by the LLM (title/tags/lists), its tags merged
    with the deterministic metadata tags, its note built from the metadata, and
    its media downloaded from the CDN and uploaded to Karakeep.
    """
    media = params.media

    # Reconcile: an already-imported bookmark only needs its missing assets, so
    # skip the LLM call entirely.
    if params.reconcile:
        existing = karakeep.find_existing_bookmark(media)
        if existing is not None:
            outcome = karakeep.complete_existing(media, existing)
            activity.logger.info("~ https://www.instagram.com/p/%s/", media.code)
            return outcome

    lists = karakeep.fetch_lists()
    enrichment = await enrich_media(media, list_names=[item["name"] for item in lists])
    final = EnrichedBookmark(
        title=enrichment.title,
        note=build_note(media) or enrichment.note,
        tags=merge_tags(derive_tags(media), enrichment.tags),
        lists=enrichment.lists,
    )
    outcome = karakeep.create_bookmark(media, final)
    if outcome.status == "failed":
        activity.logger.error("Karakeep push failed for pk=%s", media.pk)
    else:
        activity.logger.info("+ https://www.instagram.com/p/%s/", media.code)
    return outcome


@activity.defn
async def load_seen() -> list[str]:
    seen = state.load_seen()
    if not seen:
        activity.logger.info("Starting with an empty seen-state.")
    return seen


@activity.defn
async def save_seen(seen: list[str]) -> None:
    state.save_seen(seen)
