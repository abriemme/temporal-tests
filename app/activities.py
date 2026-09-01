"""Activities for the example project."""

from __future__ import annotations

from dataclasses import dataclass

from temporalio import activity


@dataclass
class GreetingInput:
    name: str


@activity.defn
async def compose_greeting(payload: GreetingInput) -> str:
    """Build a greeting message.

    A "pure" activity is enough for the example; the point is to show the
    workflow -> activity flow in the history replayed by the replay tests.
    """
    activity.logger.info("Composing greeting for %s", payload.name)
    return f"Hello, {payload.name}!"


@activity.defn
async def shout(text: str) -> str:
    """Second activity, added behind a ``workflow.patched`` on the workflow side."""
    activity.logger.info("Shouting: %s", text)
    return text.upper()
