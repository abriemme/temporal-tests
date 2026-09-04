"""Sync workflow: Instagram saved posts -> Karakeep bookmarks.

Everything that was home-rolled in the n8n script (HTTP trigger endpoint,
threads, file-based cooldown, manual retries) is expressed here as plain
orchestration: activities + timers, persisted and replayable by Temporal.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from app.sync.activities import fetch_saved, load_seen, push_to_karakeep, save_seen
    from app.sync.models import (
        CHALLENGE_ERROR_TYPE,
        MediaItem,
        SyncInput,
        SyncParams,
        SyncSummary,
    )

# Pacing between two Karakeep pushes. Must be deterministic (no random in a
# workflow).
PUSH_DELAY = timedelta(seconds=2)


def _is_challenge(exc: Exception) -> bool:
    return (
        isinstance(exc, ActivityError)
        and isinstance(exc.cause, ApplicationError)
        and exc.cause.type == CHALLENGE_ERROR_TYPE
    )


@workflow.defn
class IgSyncWorkflow:
    @workflow.run
    async def run(self, params: SyncInput) -> SyncSummary:
        seen: set[str] = set(
            await workflow.execute_activity(
                load_seen,
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        )

        try:
            medias: list[MediaItem] = await workflow.execute_activity(
                fetch_saved,
                SyncParams(backfill=params.backfill, max_items=params.max_items),
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except ActivityError as exc:
            if not _is_challenge(exc):
                raise
            # Circuit breaker: freeze for the cooldown period instead of
            # writing a cooldown.json file. A timer survives worker restarts.
            await workflow.sleep(timedelta(hours=params.cooldown_hours))
            return SyncSummary(status="cooldown")

        # Instagram returns newest-first; bookmark oldest-first so the
        # Karakeep timeline makes sense.
        new_items = [m for m in medias if str(m.pk) not in seen]
        new_items.reverse()

        summary = SyncSummary(status="ok", fetched=len(medias))
        for media in new_items:
            pushed: bool = await workflow.execute_activity(
                push_to_karakeep,
                media,
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            if pushed:
                seen.add(str(media.pk))
                summary.imported += 1
                # Incremental persistence: a crash loses nothing.
                await workflow.execute_activity(
                    save_seen,
                    sorted(seen),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            else:
                summary.failed += 1
            await workflow.sleep(PUSH_DELAY)

        return summary
