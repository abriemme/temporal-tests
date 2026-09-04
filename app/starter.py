"""Start a sync manually or (re)create the nightly schedule.

Replaces both n8n triggers of the old script:
- the nightly cron  -> Temporal Schedule;
- the ``POST /sync`` HTTP endpoint -> ``uv run python -m app.starter sync``.
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from temporalio.client import Client, Schedule, ScheduleActionStartWorkflow, ScheduleSpec
from temporalio.common import RetryPolicy

from app.config import TASK_QUEUE, TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE
from app.sync import IgSyncWorkflow, SyncInput

SCHEDULE_ID = "ig-to-karakeep-nightly"


async def _client() -> Client:
    return await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)


async def sync(args: list[str]) -> None:
    backfill = "--backfill" in args
    handle = await (await _client()).start_workflow(
        IgSyncWorkflow.run,
        SyncInput(backfill=backfill),
        id=f"ig-sync-{'backfill' if backfill else 'run'}-{uuid.uuid4().hex[:8]}",
        task_queue=TASK_QUEUE,
    )
    print(f"started {handle.id}")
    print(await handle.result())


async def schedule(args: list[str]) -> None:
    client = await _client()
    action = ScheduleActionStartWorkflow(
        IgSyncWorkflow.run,
        SyncInput(),
        id="ig-sync-scheduled",
        task_queue=TASK_QUEUE,
        retry_policy=RetryPolicy(maximum_attempts=3),
    )
    handle = await client.create_schedule(
        SCHEDULE_ID,
        Schedule(
            action=action,
            spec=ScheduleSpec(cron_expressions=["0 3 * * *"]),
        ),
    )
    print(f"schedule {handle.id} created (03:00 daily)")


async def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "sync"
    args = sys.argv[2:]
    if command == "sync":
        await sync(args)
    elif command == "schedule":
        await schedule(args)
    else:
        sys.exit(f"unknown command: {command}")


if __name__ == "__main__":
    asyncio.run(main())
