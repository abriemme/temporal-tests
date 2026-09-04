"""Tests for the Instagram -> Karakeep sync workflow (time-skipping).

The real activities (instagrapi / Karakeep HTTP calls) are replaced by fakes
registered under the same activity names: the workflow logic under test is the
orchestration (dedup, ordering, pacing, cooldown, reconcile), not the network
calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from app.config import TASK_QUEUE
from app.sync import IgSyncWorkflow, SyncInput
from tests.conftest import new_workflow_id

CHALLENGE_TYPE = "ChallengeDetected"


@dataclass
class SyncMocks:
    fetched: list = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    pushed: list = field(default_factory=list)
    saved_seen: list[list[str]] = field(default_factory=list)
    push_results: list[str] = field(default_factory=list)
    challenge_on_fetch: bool = False


def _make_activities(mocks: SyncMocks) -> list:
    @activity.defn(name="fetch_saved_page")
    async def fetch_saved_page(params) -> dict:
        if mocks.challenge_on_fetch:
            from temporalio.exceptions import ApplicationError

            raise ApplicationError("Instagram challenge", type=CHALLENGE_TYPE)
        # One page, then stop (empty cursor).
        return {"items": mocks.fetched, "next_cursor": ""}

    @activity.defn(name="load_seen")
    async def load_seen() -> list[str]:
        return sorted(mocks.seen)

    @activity.defn(name="save_seen")
    async def save_seen(seen: list[str]) -> None:
        mocks.saved_seen.append(seen)

    @activity.defn(name="push_to_karakeep")
    async def push_to_karakeep(params) -> dict:
        mocks.pushed.append(params["media"])
        status = mocks.push_results.pop(0) if mocks.push_results else "imported"
        return {"status": status, "assets": {}, "lists": []}

    return [fetch_saved_page, load_seen, save_seen, push_to_karakeep]


def _media(pk: str, code: str | None = None) -> dict:
    return {
        "pk": pk,
        "code": code or f"c{pk}",
        "username": "someone",
        "caption": "a caption",
    }


async def _run(env: WorkflowEnvironment, mocks: SyncMocks, **input_kwargs):
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[IgSyncWorkflow],
        activities=_make_activities(mocks),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        return await env.client.execute_workflow(
            IgSyncWorkflow.run,
            SyncInput(**input_kwargs),
            id=new_workflow_id("ig-sync"),
            task_queue=TASK_QUEUE,
        )


@pytest.mark.asyncio
async def test_sync_imports_new_posts_oldest_first(
    time_skipping_env: WorkflowEnvironment,
) -> None:
    """Instagram returns newest-first; Karakeep must receive oldest-first."""
    mocks = SyncMocks(
        fetched=[_media("3"), _media("2"), _media("1")],
        seen={"3"},  # already bookmarked
    )

    result = await _run(time_skipping_env, mocks)

    assert [m["pk"] for m in mocks.pushed] == ["1", "2"]
    assert result.status == "ok"
    assert result.fetched == 3
    assert result.imported == 2
    assert result.failed == 0
    # Seen state is persisted incrementally after each success.
    assert mocks.saved_seen[-1] == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_sync_with_no_new_posts_pushes_nothing(
    time_skipping_env: WorkflowEnvironment,
) -> None:
    mocks = SyncMocks(fetched=[_media("1"), _media("2")], seen={"1", "2"})

    result = await _run(time_skipping_env, mocks)

    assert mocks.pushed == []
    assert result.imported == 0


@pytest.mark.asyncio
async def test_push_failure_is_counted_and_not_marked_seen(
    time_skipping_env: WorkflowEnvironment,
) -> None:
    mocks = SyncMocks(
        # Instagram returns newest-first: "1" is the oldest of the two.
        fetched=[_media("2"), _media("1")],
        push_results=["failed", "imported"],
    )

    result = await _run(time_skipping_env, mocks)

    assert result.imported == 1
    assert result.failed == 1
    # Only the successful post is persisted as seen.
    assert mocks.saved_seen == [["2"]]


@pytest.mark.asyncio
async def test_reconcile_completes_ignoring_seen_state(
    time_skipping_env: WorkflowEnvironment,
) -> None:
    """Reconcile reprocesses every fetched post, even already-seen ones."""
    mocks = SyncMocks(
        fetched=[_media("1")],
        seen={"1"},  # would be skipped in a normal run
        push_results=["completed"],
    )

    result = await _run(time_skipping_env, mocks, reconcile=True)

    assert [m["pk"] for m in mocks.pushed] == ["1"]
    assert result.completed == 1
    assert result.imported == 0
    assert mocks.saved_seen == [["1"]]


@pytest.mark.asyncio
async def test_challenge_triggers_cooldown_without_pushing(
    time_skipping_env: WorkflowEnvironment,
) -> None:
    """A challenge from Instagram must freeze the sync (skipped timer in test)."""
    mocks = SyncMocks(challenge_on_fetch=True)

    result = await _run(time_skipping_env, mocks, cooldown_hours=24.0)

    assert result.status == "cooldown"
    assert mocks.pushed == []
    assert mocks.saved_seen == []
