"""Fixtures partagées par la suite de tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from temporalio.testing import WorkflowEnvironment


@pytest_asyncio.fixture
async def time_skipping_env() -> AsyncIterator[WorkflowEnvironment]:
    """Serveur de test Temporal avec time-skipping automatique.

    ``start_time_skipping()`` télécharge/lance le test server intégré et avance
    l'horloge automatiquement dès que tous les workers sont en attente d'un
    timer : un ``workflow.sleep(days=1)`` se résout instantanément.
    """
    env = await WorkflowEnvironment.start_time_skipping()
    try:
        yield env
    finally:
        await env.shutdown()


def new_workflow_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"
