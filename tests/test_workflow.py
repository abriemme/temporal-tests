"""Tests des workflows via le serveur de test avec time-skipping."""

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

    # Le patch ``greeting-shout-v2`` est actif -> message en majuscules.
    assert result == "HELLO, WORLD!"


@pytest.mark.asyncio
async def test_sleepy_workflow_skips_time(
    time_skipping_env: WorkflowEnvironment,
) -> None:
    """Le workflow dort 1 jour ; le test doit passer en quelques millisecondes.

    C'est tout l'intérêt de ``start_time_skipping()`` : l'horloge du serveur de
    test saute le timer sans attendre le temps réel.
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
