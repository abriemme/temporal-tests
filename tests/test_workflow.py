"""Workflow tests using the test server with time-skipping."""

from __future__ import annotations

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.activities import compose_greeting, shout
from app.config import TASK_QUEUE
from app.workflows import GreetingWorkflow, SleepyGreetingWorkflow
from tests.conftest import new_workflow_id


@pytest.mark.asyncio
async def test_greeting_workflow(time_skipping_env: WorkflowEnvironment) -> None:
    async with Worker(
        time_skipping_env.client,
        task_queue=TASK_QUEUE,
        workflows=[GreetingWorkflow],
        activities=[compose_greeting, shout],
    ):
        result = await time_skipping_env.client.execute_workflow(
            GreetingWorkflow.run,
            "World",
            id=new_workflow_id("greeting"),
            task_queue=TASK_QUEUE,
        )

    # The ``greeting-shout-v2`` patch is active -> uppercase message.
    assert result == "HELLO, WORLD!"


@pytest.mark.asyncio
async def test_sleepy_workflow_skips_time(
    time_skipping_env: WorkflowEnvironment,
) -> None:
    """The workflow sleeps 1 day; the test must pass in a few milliseconds.

    This is the whole point of ``start_time_skipping()``: the test server's clock
    skips the timer without waiting for real time.
    """
    async with Worker(
        time_skipping_env.client,
        task_queue=TASK_QUEUE,
        workflows=[SleepyGreetingWorkflow],
        activities=[compose_greeting],
    ):
        result = await time_skipping_env.client.execute_workflow(
            SleepyGreetingWorkflow.run,
            "Temporal",
            id=new_workflow_id("sleepy"),
            task_queue=TASK_QUEUE,
        )

    assert result == "Hello, Temporal!"
