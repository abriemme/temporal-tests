"""Shared data structures for the sync (models + error contract).

Kept free of any I/O or Temporal decorator so both the workflow sandbox and
the activity side can import them safely.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import MAX_ITEMS

# ApplicationError "type" raised by the Instagram activity when the account is
# challenged/rate-limited; the workflow reacts by cooling down.
CHALLENGE_ERROR_TYPE = "ChallengeDetected"


@dataclass
class SyncParams:
    """Input of the ``fetch_saved`` activity."""

    backfill: bool = False
    max_items: int = MAX_ITEMS


@dataclass
class SyncInput:
    """Input of ``IgSyncWorkflow``."""

    backfill: bool = False
    max_items: int = 40
    cooldown_hours: float = 24.0


@dataclass
class Enrichment:
    """Structured LLM output for one Instagram post (title, summary, tags)."""

    title: str
    note: str
    tags: list[str]


@dataclass
class MediaItem:
    """One Instagram saved post, flattened for serialization."""

    pk: str
    code: str
    username: str
    caption: str


@dataclass
class SyncSummary:
    """Result of ``IgSyncWorkflow``."""

    status: str  # "ok" | "cooldown"
    fetched: int = 0
    imported: int = 0
    failed: int = 0
