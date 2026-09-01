"""Fixtures shared by the test suite."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from temporalio.testing import WorkflowEnvironment


@pytest_asyncio.fixture
async def time_skipping_env() -> AsyncIterator[WorkflowEnvironment]:
    """Temporal test server with automatic time-skipping.

    ``start_time_skipping()`` downloads/starts the bundled test server and
    advances the clock automatically as soon as all workers are waiting on a
    timer: a ``workflow.sleep(days=1)`` resolves instantly.
    """
    env = await WorkflowEnvironment.start_time_skipping()
    try:
        yield env
    finally:
        await env.shutdown()


def new_workflow_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"
