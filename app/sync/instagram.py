"""Instagram access (instagrapi): session, saved-posts fetch, collections.

Pure service layer: no Temporal decorators here. instagrapi is an optional
dependency (group "ig"), imported lazily so the worker tests don't need it.
"""

from __future__ import annotations

import json
import time

from app.config import (
    COLLECTION_CACHE_FILE,
    COLLECTION_CACHE_HOURS,
    COLLECTION_SCAN,
    SESSION_FILE,
    SYNC_COLLECTIONS,
)
from app.sync.models import FetchPageResult, MediaItem, MediaResource

# Markers that an Instagram error is actually a challenge/checkpoint/rate
# limit: the right reaction is to stop and cool down, not to retry.
CHALLENGE_MARKERS = ("challenge", "checkpoint", "467", "feedback_required")

# instagrapi collection id for the "All posts" saved collection.
SAVED_COLLECTION = "ALL_MEDIA_AUTO_COLLECTION"

# Per-process caches: rebuilt once per worker, not per page/post.
_collection_names_by_id: dict[str, str] | None = None
_collections_by_media: dict[str, set[str]] | None = None


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
        raise RuntimeError(f"No session in {SESSION_FILE}. Run the login CLI first.")
    cl.load_settings(SESSION_FILE)
    return cl


def _clips_music(media) -> tuple[str | None, str | None]:
    """Best-effort extraction of the Reel's music title/artist."""
    clips = getattr(media, "clips_metadata", None) or {}
    if not isinstance(clips, dict):
        clips = getattr(clips, "__dict__", {}) or {}
    music = (clips.get("music_info") or {}) if isinstance(clips, dict) else {}
    if music and not isinstance(music, dict):
        music = getattr(music, "__dict__", {}) or {}
    asset = (music.get("music_asset_info") or {}) if isinstance(music, dict) else {}
    if asset and not isinstance(asset, dict):
        asset = getattr(asset, "__dict__", {}) or {}
    return asset.get("title"), asset.get("display_artist")


def _str_or_none(value) -> str | None:
    return str(value) if value else None


def flatten_media(media) -> MediaItem:
    """Flatten an instagrapi ``Media`` into a serializable ``MediaItem``.

    Everything is defensive getattr: available fields vary by post type and
    instagrapi version, and a missing field must never fail an import.
    """
    user = getattr(media, "user", None)
    location = getattr(media, "location", None)
    music_title, music_artist = _clips_music(media)
    taken_at = getattr(media, "taken_at", None)

    resources = [
        MediaResource(
            thumbnail_url=_str_or_none(getattr(res, "thumbnail_url", None)),
            video_url=_str_or_none(getattr(res, "video_url", None)),
        )
        for res in (getattr(media, "resources", None) or [])
    ]

    return MediaItem(
        pk=str(media.pk),
        code=media.code,
        username=getattr(user, "username", None) or "?",
        caption=(getattr(media, "caption_text", "") or "").strip(),
        full_name=getattr(user, "full_name", None),
        alt_text=getattr(media, "accessibility_caption", None),
        product_type=getattr(media, "product_type", None),
        media_type=getattr(media, "media_type", None),
        taken_at=taken_at.isoformat() if hasattr(taken_at, "isoformat") else None,
        duration=getattr(media, "video_duration", None),
        location_name=getattr(location, "name", None),
        location_city=getattr(location, "city", None),
        music_title=music_title,
        music_artist=music_artist,
        thumbnail_url=_str_or_none(getattr(media, "thumbnail_url", None)),
        video_url=_str_or_none(getattr(media, "video_url", None)),
        profile_pic_url=_str_or_none(getattr(user, "profile_pic_url", None)),
        resources=resources,
    )


# --- Collection membership ---------------------------------------------------


def inline_collection_ids(cl) -> dict[str, list[str]]:
    """Read collection membership from the last private call's raw payload.

    The saved feed frequently carries ``saved_collection_ids`` on each item;
    when it does, membership is already paid for. instagrapi's Media model
    drops the field, hence reading ``cl.last_json`` directly.
    """
    raw = getattr(cl, "last_json", None)
    if not isinstance(raw, dict):
        return {}

    out: dict[str, list[str]] = {}
    for item in raw.get("items") or []:
        if not isinstance(item, dict):
            continue
        media = item.get("media") if isinstance(item.get("media"), dict) else item
        pk = media.get("pk") or (media.get("id") or "").split("_")[0]
        ids = media.get("saved_collection_ids") or item.get("saved_collection_ids")
        if pk and ids:
            out[str(pk)] = [str(i) for i in ids]
    return out


def _names_by_id(cl) -> dict[str, str]:
    """Collection id -> name. One private-API call, cached per worker."""
    global _collection_names_by_id
    if _collection_names_by_id is not None:
        return _collection_names_by_id
    try:
        _collection_names_by_id = {
            str(c.id): c.name for c in cl.collections() if str(c.id) != SAVED_COLLECTION
        }
    except Exception:
        _collection_names_by_id = {}
    return _collection_names_by_id


def _load_collection_cache() -> dict[str, set[str]] | None:
    if COLLECTION_CACHE_HOURS <= 0 or not COLLECTION_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(COLLECTION_CACHE_FILE.read_text())
        age = time.time() - data["fetched_at"]
    except json.JSONDecodeError, KeyError, TypeError:
        return None
    if age > COLLECTION_CACHE_HOURS * 3600:
        return None
    return {k: set(v) for k, v in data["map"].items()}


def _save_collection_cache(mapping: dict[str, set[str]]) -> None:
    COLLECTION_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COLLECTION_CACHE_FILE.write_text(
        json.dumps(
            {
                "fetched_at": time.time(),
                "map": {k: sorted(v) for k, v in mapping.items()},
            }
        )
    )


def _collections_by_media_map(cl) -> dict[str, set[str]]:
    """media_pk -> collection names, by scanning each collection.

    Instagram exposes no reverse lookup, so this costs one private-API call per
    collection. Cached to disk (see COLLECTION_CACHE_HOURS) and per worker.
    """
    global _collections_by_media
    if _collections_by_media is not None:
        return _collections_by_media

    cached = _load_collection_cache()
    if cached is not None:
        _collections_by_media = cached
        return cached

    mapping: dict[str, set[str]] = {}
    try:
        collections = cl.collections()
    except Exception:
        _collections_by_media = mapping
        return mapping

    for col in collections:
        if str(col.id) == SAVED_COLLECTION:
            continue
        try:
            medias = cl.collection_medias(col.id, amount=COLLECTION_SCAN)
        except Exception:
            continue
        for media in medias:
            mapping.setdefault(str(media.pk), set()).add(col.name)

    _save_collection_cache(mapping)
    _collections_by_media = mapping
    return mapping


def _annotate_collections(cl, items: list[MediaItem]) -> None:
    """Populate each item's ``collection_names`` in place."""
    if not SYNC_COLLECTIONS or not items:
        return

    inline = inline_collection_ids(cl)
    if inline:
        names_by_id = _names_by_id(cl)
        for item in items:
            item.collection_names = sorted(
                {names_by_id[i] for i in inline.get(item.pk, ()) if i in names_by_id}
            )
    else:
        mapping = _collections_by_media_map(cl)
        for item in items:
            item.collection_names = sorted(mapping.get(item.pk, set()))


# --- Fetch -------------------------------------------------------------------


def fetch_saved_page(cursor: str, backfill: bool, max_items: int) -> FetchPageResult:
    """Fetch one page of the saved-posts collection, newest first.

    Uses the cursor-aware chunk API when available so the workflow can page
    through a backfill; falls back to a single bulk fetch otherwise. Reading
    ``cl.last_json`` for collection membership must happen right after the
    fetch, before the next private call overwrites it.
    """
    cl = get_client()
    chunk = getattr(cl, "collection_medias_v1_chunk", None)

    if chunk is None:
        # instagrapi build without cursor access: one bulk page, no next cursor.
        amount = 0 if backfill else max_items  # 0 = all, in instagrapi
        medias = cl.collection_medias(SAVED_COLLECTION, amount=amount)
        next_cursor = ""
    else:
        medias, next_cursor = chunk(SAVED_COLLECTION, max_id=cursor or "")

    items = [flatten_media(m) for m in medias]
    _annotate_collections(cl, items)
    return FetchPageResult(items=items, next_cursor=next_cursor or "")
