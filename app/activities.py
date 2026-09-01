"""Activities du projet d'exemple."""

from __future__ import annotations

from dataclasses import dataclass

from temporalio import activity


@dataclass
class GreetingInput:
    name: str


@activity.defn
async def compose_greeting(payload: GreetingInput) -> str:
    """Construit un message de salutation.

    Une activity « pure » suffit pour l'exemple ; l'important est de montrer le
    flux workflow -> activity dans l'historique rejoué par les tests de replay.
    """
    activity.logger.info("Composing greeting for %s", payload.name)
    return f"Hello, {payload.name}!"


@activity.defn
async def shout(text: str) -> str:
    """Deuxième activity, ajoutée derrière un ``workflow.patched`` côté workflow."""
    activity.logger.info("Shouting: %s", text)
    return text.upper()
