"""Sync workflow: Instagram saved posts -> Karakeep bookmarks.

Everything that was home-rolled in the n8n script (HTTP trigger endpoint,
threads, file-based cooldown, disk-persisted backfill cursor, manual retries)
is expressed here as plain orchestration: activities + timers, persisted and
replayable by Temporal. The backfill cursor lives in the workflow state, so an
interrupted run resumes where it stopped with no cursor.json on disk.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from app.sync.activities import (
        fetch_saved_page,
        load_seen,
        push_to_karakeep,
        save_seen,
    )
    from app.sync.models import (
        CHALLENGE_ERROR_TYPE,
        FetchPageParams,
        FetchPageResult,
        PushOutcome,
        PushParams,
        SyncInput,
        SyncSummary,
    )

# Pacing between two Karakeep pushes, and between two Instagram pages. Both must
# be deterministic (no random in a workflow).
PUSH_DELAY = timedelta(seconds=2)
PAGE_DELAY = timedelta(seconds=5)


def _is_challenge(exc: Exception) -> bool:
    return (
        isinstance(exc, ActivityError)
        and isinstance(exc.cause, ApplicationError)
        and exc.cause.type == CHALLENGE_ERROR_TYPE
    )


def _apply_outcome(summary: SyncSummary, outcome: PushOutcome) -> None:
    if outcome.status == "imported":
        summary.imported += 1
    elif outcome.status == "completed":
        summary.completed += 1
    elif outcome.status == "failed":
        summary.failed += 1
    for asset_type, count in outcome.assets.items():
        summary.assets[asset_type] = summary.assets.get(asset_type, 0) + count
    for name in outcome.lists:
        summary.collections[name] = summary.collections.get(name, 0) + 1


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

        summary = SyncSummary(status="ok")
        cursor = ""
        page = 0

        while page < params.max_pages:
            try:
                result: FetchPageResult = await workflow.execute_activity(
                    fetch_saved_page,
                    FetchPageParams(
                        cursor=cursor,
                        backfill=params.backfill,
                        max_items=params.max_items,
                    ),
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

            page += 1
            summary.fetched += len(result.items)

            # In reconcile mode seen.json is not the arbiter: every post is
            # confronted with the real state of Karakeep.
            if params.reconcile:
                new_items = list(result.items)
            else:
                new_items = [m for m in result.items if m.pk not in seen]

            # A fully-known page on an incremental run means we have caught up.
            if not new_items and not params.backfill and not params.reconcile:
                break

            if new_items:
                # Cap before pushing so a MAX_ITEMS=3 test never pays for more.
                if not params.backfill:
                    remaining = params.max_items - (summary.imported + summary.completed)
                    if remaining <= 0:
                        break
                    new_items = new_items[:remaining]

                # Instagram returns newest-first; bookmark oldest-first so the
                # Karakeep timeline makes sense.
                new_items.reverse()
                for media in new_items:
                    outcome: PushOutcome = await workflow.execute_activity(
                        push_to_karakeep,
                        PushParams(media=media, reconcile=params.reconcile),
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                    _apply_outcome(summary, outcome)
                    if outcome.status != "failed":
                        seen.add(media.pk)
                        # Incremental persistence: a crash loses nothing.
                        await workflow.execute_activity(
                            save_seen,
                            sorted(seen),
                            start_to_close_timeout=timedelta(seconds=10),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )
                    await workflow.sleep(PUSH_DELAY)

            done = summary.imported + summary.completed
            if not result.next_cursor:
                break
            if not params.backfill and done >= params.max_items:
                break
            cursor = result.next_cursor
            await workflow.sleep(PAGE_DELAY)

        return summary
