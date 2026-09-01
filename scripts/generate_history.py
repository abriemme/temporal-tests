"""(Re)generate the JSON histories used by the replay test.

We run the workflows on the test server (time-skipping), then export the full
history as JSON into ``tests/histories/``.

Usage::

    python scripts/generate_history.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.activities import compose_greeting, shout
from app.config import TASK_QUEUE
from app.workflows import GreetingWorkflow, SleepyGreetingWorkflow

HISTORIES_DIR = Path(__file__).resolve().parents[1] / "tests" / "histories"


async def _run_and_export(env: WorkflowEnvironment, workflow, arg: str, wf_id: str, filename: str) -> None:
    handle = await env.client.start_workflow(
        workflow.run,
        arg,
        id=wf_id,
        task_queue=TASK_QUEUE,
    )
    await handle.result()

    history = await handle.fetch_history()
    HISTORIES_DIR.mkdir(parents=True, exist_ok=True)
    out = HISTORIES_DIR / filename
    out.write_text(json.dumps(history.to_json_dict(), indent=2, sort_keys=True))
    print(f"wrote {out.relative_to(Path.cwd())}")


async def main() -> None:
    env = await WorkflowEnvironment.start_time_skipping()
    try:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[GreetingWorkflow, SleepyGreetingWorkflow],
            activities=[compose_greeting, shout],
        ):
            await _run_and_export(
                env, GreetingWorkflow, "World", "greeting-1", "greeting_workflow.json"
            )
            await _run_and_export(
                env, SleepyGreetingWorkflow, "Temporal", "sleepy-1", "sleepy_workflow.json"
            )
    finally:
        await env.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
