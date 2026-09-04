"""Shared data structures for the sync (models + error contract).

Kept free of any I/O or Temporal decorator so both the workflow sandbox and
the activity side can import them safely.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import MAX_ITEMS, MAX_PAGES

# ApplicationError "type" raised by the Instagram activity when the account is
# challenged/rate-limited; the workflow reacts by cooling down.
CHALLENGE_ERROR_TYPE = "ChallengeDetected"


@dataclass
class FetchPageParams:
    """Input of the ``fetch_saved_page`` activity: one page at a time."""

    cursor: str = ""
    backfill: bool = False
    max_items: int = MAX_ITEMS


@dataclass
class SyncInput:
    """Input of ``IgSyncWorkflow``."""

    backfill: bool = False
    max_items: int = MAX_ITEMS
    max_pages: int = MAX_PAGES
    cooldown_hours: float = 24.0
    # Reconcile mode: instead of trusting seen.json, confront each post with the
    # real state of Karakeep. An existing bookmark missing a thumbnail is
    # completed rather than skipped. Needed for imports predating asset support,
    # whose CDN URLs have since expired.
    reconcile: bool = False


@dataclass
class EnrichedBookmark:
    """Structured LLM output for one saved post: bookmark fields + list routing.

    ``lists`` holds the exact names of the Karakeep lists the post belongs to
    (copied verbatim, accents and ampersands included); it is empty when no
    list clearly fits.
    """

    title: str
    note: str
    tags: list[str]
    lists: list[str] = field(default_factory=list)


@dataclass
class MediaResource:
    """One slide of a carousel (image or video)."""

    thumbnail_url: str | None = None
    video_url: str | None = None


@dataclass
class MediaItem:
    """One Instagram saved post, flattened for serialization.

    Everything the downstream activities need is flattened here at fetch time:
    the raw instagrapi ``Media`` object cannot cross the Temporal boundary, and
    the CDN URLs it carries are signed and expire, so they must be captured now.
    """

    pk: str
    code: str
    username: str
    caption: str
    full_name: str | None = None
    alt_text: str | None = None
    product_type: str | None = None
    media_type: int | None = None
    # ISO-8601 string (not a datetime): keeps serialization across the Temporal
    # boundary trivial. Used as the bookmark's ``createdAt``.
    taken_at: str | None = None
    duration: float | None = None
    location_name: str | None = None
    location_city: str | None = None
    music_title: str | None = None
    music_artist: str | None = None
    thumbnail_url: str | None = None
    video_url: str | None = None
    profile_pic_url: str | None = None
    resources: list[MediaResource] = field(default_factory=list)
    # Names of the Instagram collections that contain this post (resolved at
    # fetch time from the feed payload); mapped to Karakeep lists on push.
    collection_names: list[str] = field(default_factory=list)


@dataclass
class FetchPageResult:
    """Result of ``fetch_saved_page``: a page of posts + the next cursor."""

    items: list[MediaItem] = field(default_factory=list)
    next_cursor: str = ""


@dataclass
class PushParams:
    """Input of the ``push_to_karakeep`` activity."""

    media: MediaItem
    reconcile: bool = False


@dataclass
class PushOutcome:
    """Result of ``push_to_karakeep``.

    ``status`` is one of ``imported`` (new bookmark), ``completed`` (existing
    bookmark filled with missing assets, reconcile mode), ``skipped`` (existing
    bookmark already complete) or ``failed`` (creation rejected).
    """

    status: str
    assets: dict[str, int] = field(default_factory=dict)
    lists: list[str] = field(default_factory=list)


@dataclass
class SyncSummary:
    """Result of ``IgSyncWorkflow``."""

    status: str  # "ok" | "cooldown"
    fetched: int = 0
    imported: int = 0
    completed: int = 0
    failed: int = 0
    assets: dict[str, int] = field(default_factory=dict)
    collections: dict[str, int] = field(default_factory=dict)
