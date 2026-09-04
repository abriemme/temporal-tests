"""Instagram access (instagrapi): session loading and saved-posts fetch.

Pure service layer: no Temporal decorators here. instagrapi is an optional
dependency (group "ig"), imported lazily so the worker tests don't need it.
"""

from __future__ import annotations

from app.config import SESSION_FILE
from app.sync.models import MediaItem, SyncParams

# Markers that an Instagram error is actually a challenge/checkpoint/rate
# limit: the right reaction is to stop and cool down, not to retry.
CHALLENGE_MARKERS = ("challenge", "checkpoint", "467", "feedback_required")


def is_challenge_error(exc: Exception) -> bool:
    return any(marker in str(exc).lower() for marker in CHALLENGE_MARKERS)


def get_client():
    """Load the persisted instagrapi session (login is a separate CLI step)."""
    try:
        from instagrapi import Client
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "instagrapi is not installed. Run `uv sync --group ig`."
        ) from exc

    cl = Client()
    # Space out private-API calls; a request burst is the most obvious
    # automation signal.
    cl.delay_range = [4, 10]
    if not SESSION_FILE.exists():
        raise RuntimeError(
            f"No session in {SESSION_FILE}. Run the login CLI first."
        )
    cl.load_settings(SESSION_FILE)
    return cl


def fetch_saved_media(params: SyncParams) -> list[MediaItem]:
    """Fetch the saved-posts collection, newest first."""
    cl = get_client()
    try:
        amount = 0 if params.backfill else params.max_items  # 0 = all
        medias = cl.collection_medias("ALL_MEDIA_AUTO_COLLECTION", amount=amount)
    except Exception as exc:
        # Bubble the original error up; the activity layer decides whether it
        # is a challenge and wraps it accordingly.
        raise

    return [
        MediaItem(
            pk=str(m.pk),
            code=m.code,
            username=getattr(m.user, "username", "?") or "?",
            caption=(m.caption_text or "").strip(),
        )
        for m in medias
    ]
