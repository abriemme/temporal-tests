"""Workflow tests using the test server with time-skipping."""

from __future__ import annotations

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.activities import GreetingInput, compose_greeting, shout
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


@pytest.mark.asyncio
async def test_greeting_workflow_with_mocked_activities(
    time_skipping_env: WorkflowEnvironment,
) -> None:
    """Test the workflow logic in isolation by mocking its activities.

    An activity registered with the *same name* as the real one replaces it on
    the worker. This lets us assert on the workflow's orchestration (it calls
    ``compose_greeting`` then ``shout``) without running the real activity
    implementations — the standard way to test workflows against edge cases or
    failures the real activities can't easily produce.
    """

    @activity.defn(name="compose_greeting")
    async def compose_greeting_mocked(payload: GreetingInput) -> str:
        return f"Bonjour, {payload.name}!"

    @activity.defn(name="shout")
    async def shout_mocked(text: str) -> str:
        return f"<<{text}>>"

    async with Worker(
        time_skipping_env.client,
        task_queue=TASK_QUEUE,
        workflows=[GreetingWorkflow],
        activities=[compose_greeting_mocked, shout_mocked],
    ):
        result = await time_skipping_env.client.execute_workflow(
            GreetingWorkflow.run,
            "World",
            id=new_workflow_id("greeting-mocked"),
            task_queue=TASK_QUEUE,
        )

    # compose_greeting -> "Bonjour, World!" then shout -> wrapped in <<...>>.
    assert result == "<<Bonjour, World!>>"
