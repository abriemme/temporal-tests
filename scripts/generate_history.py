"""(Re)generate the JSON history used by the replay test.

We run IgSyncWorkflow on the test server (time-skipping) with stub activities,
then export the full history as JSON into ``tests/histories/``.

Usage::

    python scripts/generate_history.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.config import TASK_QUEUE
from app.sync_workflow import IgSyncWorkflow, SyncInput

HISTORIES_DIR = Path(__file__).resolve().parents[1] / "tests" / "histories"


@activity.defn(name="fetch_saved")
async def fetch_saved(params) -> list:
    return [
        {"pk": "2", "code": "c2", "username": "a", "caption": "new"},
        {"pk": "1", "code": "c1", "username": "a", "caption": "old"},
    ]


@activity.defn(name="load_seen")
async def load_seen() -> list[str]:
    return []


@activity.defn(name="save_seen")
async def save_seen(seen: list[str]) -> None:
    pass


@activity.defn(name="push_to_karakeep")
async def push_to_karakeep(media) -> bool:
    return True


async def main() -> None:
    env = await WorkflowEnvironment.start_time_skipping()
    try:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[IgSyncWorkflow],
            activities=[fetch_saved, load_seen, save_seen, push_to_karakeep],
        ):
            handle = await env.client.start_workflow(
                IgSyncWorkflow.run,
                SyncInput(),
                id="ig-sync-1",
                task_queue=TASK_QUEUE,
            )
            await handle.result()

            history = await handle.fetch_history()
            HISTORIES_DIR.mkdir(parents=True, exist_ok=True)
            out = HISTORIES_DIR / "ig_sync_workflow.json"
            out.write_text(json.dumps(history.to_json_dict(), indent=2, sort_keys=True))
            print(f"wrote {out.relative_to(Path.cwd())}")
    finally:
        await env.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
