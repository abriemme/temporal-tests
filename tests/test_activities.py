"""Activity unit tests using ``ActivityEnvironment``.

``ActivityEnvironment`` runs an activity function directly, outside of any
worker or workflow, while still providing a valid ``activity`` context (logger,
``activity.info()``, heartbeats, cancellation). This is the fast, isolated way
to test the business logic of an activity.

See https://docs.temporal.io/develop/python/best-practices/testing-suite.
"""

from __future__ import annotations

import asyncio

import pytest
from temporalio import activity
from temporalio.testing import ActivityEnvironment

from app.activities import GreetingInput, compose_greeting, shout


@pytest.mark.asyncio
async def test_compose_greeting() -> None:
    env = ActivityEnvironment()
    result = await env.run(compose_greeting, GreetingInput("World"))
    assert result == "Hello, World!"


@pytest.mark.asyncio
async def test_shout() -> None:
    env = ActivityEnvironment()
    result = await env.run(shout, "Hello, World!")
    assert result == "HELLO, WORLD!"


@pytest.mark.asyncio
async def test_activity_heartbeats() -> None:
    """``on_heartbeat`` captures the details passed to ``activity.heartbeat()``."""

    @activity.defn
    async def heartbeating_activity(count: int) -> None:
        for i in range(count):
            activity.heartbeat(i)

    heartbeats: list[int] = []
    env = ActivityEnvironment()
    env.on_heartbeat = lambda *args: heartbeats.append(args[0])

    await env.run(heartbeating_activity, 3)

    assert heartbeats == [0, 1, 2]


@pytest.mark.asyncio
async def test_activity_cancellation() -> None:
    """``env.cancel()`` propagates cancellation into the running activity."""

    @activity.defn
    async def cancellable_activity() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(0.01)

    env = ActivityEnvironment()

    async def cancel_soon() -> None:
        await asyncio.sleep(0.05)
        env.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.gather(env.run(cancellable_activity), cancel_soon())
