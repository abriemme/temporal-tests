"""Karakeep access: bookmark creation, assets, list membership, reconcile."""

from __future__ import annotations

import contextlib

import requests

from app.config import (
    ASSET_TYPES,
    CREATE_MISSING_LISTS,
    KARAKEEP_LIST_ID,
    KARAKEEP_TOKEN,
    KARAKEEP_URL,
    MAX_ASSET_MB,
    MAX_CAROUSEL,
    REPLACE_BANNER,
)
from app.sync.enrich import TITLE_MAX
from app.sync.facts import NOTE_MAX
from app.sync.models import EnrichedBookmark, MediaItem, PushOutcome

TIMEOUT = 30

# Karakeep lists are stable within a run; fetched once per worker process and
# reused so every bookmark sees the same names.
_lists_cache: list[dict] | None = None
# Reconcile mode only: URL -> existing bookmark, built lazily once per worker.
_bookmarks_by_url: dict[str, dict] | None = None


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {KARAKEEP_TOKEN}",
        "Content-Type": "application/json",
    }


# --- Lists -------------------------------------------------------------------


def _raw_lists(*, force: bool = False) -> list[dict]:
    """Return the account's raw list entries, cached per process."""
    global _lists_cache
    if _lists_cache is not None and not force:
        return _lists_cache
    try:
        r = requests.get(
            f"{KARAKEEP_URL}/api/v1/lists",
            headers=_headers(),
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
        raw = payload.get("lists", []) if isinstance(payload, dict) else payload
        _lists_cache = [{"id": item["id"], "name": item["name"]} for item in raw]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        _lists_cache = []
    return _lists_cache


def fetch_lists(*, force: bool = False) -> list[dict]:
    """Return the account's lists as ``[{"id", "name"}]`` (for the classifier)."""
    return _raw_lists(force=force)


def normalise(name: str) -> str:
    """Name comparison tolerant of whitespace and case."""
    return " ".join(name.split()).casefold()


def list_index() -> dict[str, list[str]]:
    """normalised name -> list ids. Hierarchy-agnostic: a list is matched by
    name wherever it sits in the Karakeep tree."""
    index: dict[str, list[str]] = {}
    for entry in _raw_lists():
        index.setdefault(normalise(entry["name"]), []).append(entry["id"])
    return index


def create_list(name: str, parent_id: str | None = None) -> str | None:
    """Create a Karakeep list and refresh the cache."""
    body: dict = {"name": name, "icon": "📱"}
    if parent_id:
        body["parentId"] = parent_id
    try:
        r = requests.post(
            f"{KARAKEEP_URL}/api/v1/lists",
            json=body,
            headers=_headers(),
            timeout=TIMEOUT,
        )
        r.raise_for_status()
    except requests.RequestException:
        return None
    new_id = r.json().get("id")
    if new_id and _lists_cache is not None:
        _lists_cache.append({"id": new_id, "name": name})
    return new_id


def _resolve_list(name: str, index: dict[str, list[str]]) -> str | None:
    """Find — or optionally create — the list matching a collection name."""
    ids = index.get(normalise(name), [])
    if len(ids) == 1:
        return ids[0]
    if len(ids) > 1:
        # Two lists of the same name in different places: picking one at random
        # would scatter bookmarks. Abstain.
        return None
    if not CREATE_MISSING_LISTS:
        return None
    new_id = create_list(name)
    if new_id:
        index.setdefault(normalise(name), []).append(new_id)
    return new_id


def _target_lists(
    media: MediaItem, enrichment: EnrichedBookmark
) -> list[tuple[str, str]]:
    """Resolve the LLM-chosen and collection list names to ``(id, name)``.

    Order-preserving and deduplicated: classified lists first (LLM then
    collection), then ``KARAKEEP_LIST_ID`` (added to every bookmark when set).
    """
    index = list_index()
    names: list[str] = []
    for name in [*enrichment.lists, *media.collection_names]:
        if name not in names:
            names.append(name)

    resolved: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for name in names:
        list_id = _resolve_list(name, index)
        if list_id and list_id not in seen_ids:
            seen_ids.add(list_id)
            resolved.append((list_id, name))
    if KARAKEEP_LIST_ID and KARAKEEP_LIST_ID not in seen_ids:
        resolved.append((KARAKEEP_LIST_ID, ""))
    return resolved


def attach_tags(bookmark_id: str, tags: list[str]) -> bool:
    """Attach tags to an existing bookmark (used by the retag maintenance)."""
    if not tags:
        return True
    try:
        r = requests.post(
            f"{KARAKEEP_URL}/api/v1/bookmarks/{bookmark_id}/tags",
            json={"tags": [{"tagName": t} for t in tags]},
            headers=_headers(),
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        return False
    return r.status_code in (200, 201)


def _add_to_list(bookmark_id: str, list_id: str) -> None:
    try:
        r = requests.put(
            f"{KARAKEEP_URL}/api/v1/lists/{list_id}/bookmarks/{bookmark_id}",
            headers=_headers(),
            timeout=TIMEOUT,
        )
        if r.status_code not in (200, 204):
            raise OSError(f"list add rejected ({r.status_code})")
    except requests.RequestException as exc:
        raise OSError(f"list add failed: {exc}") from exc


# --- Assets ------------------------------------------------------------------


def download_cdn(url: str) -> bytes | None:
    """Fetch a file from the Instagram CDN.

    NOT a private-API call: the URL is already in a response we paid for, so
    the account risk is marginal, unlike a download via media_info().
    """
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            limit = int(MAX_ASSET_MB * 1024 * 1024)
            declared = r.headers.get("Content-Length")
            if declared and int(declared) > limit:
                return None
            # Content-Length may lie or be absent: bound the stream too.
            chunks, total = [], 0
            for chunk in r.iter_content(65536):
                total += len(chunk)
                if total > limit:
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
    except requests.RequestException:
        return None


def upload_asset(content: bytes, filename: str, mime: str) -> str | None:
    """Upload a binary to Karakeep and return its asset id."""
    try:
        r = requests.post(
            f"{KARAKEEP_URL}/api/v1/assets",
            files={"file": (filename, content, mime)},
            headers={"Authorization": _headers()["Authorization"]},  # no Content-Type
            timeout=180,
        )
    except requests.RequestException:
        return None
    if r.status_code not in (200, 201):
        return None
    body = r.json()
    return body.get("assetId") or body.get("id")


def attach_asset(bookmark_id: str, asset_id: str, asset_type: str) -> bool:
    try:
        r = requests.post(
            f"{KARAKEEP_URL}/api/v1/bookmarks/{bookmark_id}/assets",
            json={"id": asset_id, "assetType": asset_type},
            headers=_headers(),
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        return False
    return r.status_code in (200, 201)


def replace_asset(bookmark_id: str, old_asset_id: str, new_asset_id: str) -> bool:
    """Replace an existing asset. Karakeep answers 204 with no body."""
    try:
        r = requests.put(
            f"{KARAKEEP_URL}/api/v1/bookmarks/{bookmark_id}/assets/{old_asset_id}",
            json={"assetId": new_asset_id},
            headers=_headers(),
            timeout=60,
        )
    except requests.RequestException:
        return False
    return r.status_code in (200, 201, 204)


def find_asset(bm: dict, asset_type: str) -> str | None:
    for asset in bm.get("assets") or []:
        if asset.get("assetType") == asset_type:
            return asset.get("id") or asset.get("assetId")
    return None


def asset_jobs(bm: dict | None, media: MediaItem) -> list[dict]:
    """Build the list of assets to send for this post.

    Each job carries its source URL, its Karakeep type, and the id of the asset
    to replace when applicable. Generalising avoids one branch per type in the
    sender.
    """
    jobs: list[dict] = []
    code = media.code
    thumb = media.thumbnail_url or ""
    is_video = media.media_type == 2

    def existing(asset_type: str) -> str | None:
        return find_asset(bm, asset_type) if bm else None

    if "banner" in ASSET_TYPES and thumb:
        current = existing("bannerImage")
        if current is None or REPLACE_BANNER:
            jobs.append(
                {
                    "type": "bannerImage",
                    "replace": current if REPLACE_BANNER else None,
                    "url": thumb,
                    "filename": f"{code}.jpg",
                    "mime": "image/jpeg",
                }
            )

    if "screenshot" in ASSET_TYPES and thumb:
        # The crawler's screenshot shows the login wall: replacing it is a win.
        jobs.append(
            {
                "type": "screenshot",
                "replace": existing("screenshot"),
                "url": thumb,
                "filename": f"{code}-screenshot.jpg",
                "mime": "image/jpeg",
            }
        )

    want_video = "video" in ASSET_TYPES and is_video and media.video_url
    if want_video and existing("video") is None:
        jobs.append(
            {
                "type": "video",
                "replace": None,
                "url": media.video_url,
                "filename": f"{code}.mp4",
                "mime": "video/mp4",
            }
        )

    if "carousel" in ASSET_TYPES:
        # Extra slides have no unique Karakeep counterpart: attached as
        # userUploaded, never as a replacement.
        already = (
            sum(
                1
                for a in (bm.get("assets") or [])
                if a.get("assetType") == "userUploaded"
            )
            if bm
            else 0
        )
        if not already:
            for index, res in enumerate(media.resources[:MAX_CAROUSEL], start=1):
                if res.video_url and "video" in ASSET_TYPES:
                    url, ext, mime = res.video_url, "mp4", "video/mp4"
                elif res.thumbnail_url:
                    url, ext, mime = res.thumbnail_url, "jpg", "image/jpeg"
                else:
                    continue
                jobs.append(
                    {
                        "type": "userUploaded",
                        "replace": None,
                        "url": url,
                        "filename": f"{code}-{index}.{ext}",
                        "mime": mime,
                    }
                )

    if "avatar" in ASSET_TYPES and existing("avatar") is None and media.profile_pic_url:
        jobs.append(
            {
                "type": "avatar",
                "replace": None,
                "url": media.profile_pic_url,
                "filename": f"{media.username}.jpg",
                "mime": "image/jpeg",
            }
        )

    return jobs


def push_assets(bookmark_id: str, jobs: list[dict]) -> dict[str, int]:
    """Run the asset jobs: CDN download, upload, attach/replace."""
    done: dict[str, int] = {}
    for job in jobs:
        content = download_cdn(job["url"])
        if not content:
            continue
        new_id = upload_asset(content, job["filename"], job["mime"])
        if not new_id:
            continue
        if job["replace"]:
            ok = replace_asset(bookmark_id, job["replace"], new_id)
        else:
            ok = attach_asset(bookmark_id, new_id, job["type"])
        if ok:
            done[job["type"]] = done.get(job["type"], 0) + 1
    return done


# --- Bookmarks ---------------------------------------------------------------


def build_payload(media: MediaItem, enrichment: EnrichedBookmark) -> dict:
    """Build the bookmark payload; Karakeep cannot crawl Instagram itself.

    ``enrichment`` provides the title/tags (LLM) and the merged note; the
    publication date becomes ``createdAt`` so Karakeep's timeline reflects the
    content, not the moment of the sync.
    """
    payload = {
        "type": "link",
        "url": f"https://www.instagram.com/p/{media.code}/",
        "title": enrichment.title[:TITLE_MAX],
        "note": enrichment.note[:NOTE_MAX],
        "tags": [t.strip().lower() for t in enrichment.tags if t.strip()],
    }
    if media.taken_at:
        payload["createdAt"] = media.taken_at
    return payload


def create_bookmark(media: MediaItem, enrichment: EnrichedBookmark) -> PushOutcome:
    """Create the bookmark, upload its assets and route it to its lists."""
    payload = build_payload(media, enrichment)
    try:
        r = requests.post(
            f"{KARAKEEP_URL}/api/v1/bookmarks",
            json=payload,
            headers=_headers(),
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        return PushOutcome(status="failed")

    if r.status_code not in (200, 201):
        return PushOutcome(status="failed")

    bookmark_id = r.json().get("id")
    if not bookmark_id:
        return PushOutcome(status="imported")

    assets = push_assets(bookmark_id, asset_jobs(None, media))
    placed: list[str] = []
    for list_id, name in _target_lists(media, enrichment):
        # The bookmark exists; failing the whole push on a list add would
        # duplicate it on retry.
        with contextlib.suppress(OSError):
            _add_to_list(bookmark_id, list_id)
            if name:
                placed.append(name)
    return PushOutcome(status="imported", assets=assets, lists=placed)


# --- Reconcile ---------------------------------------------------------------


def _normalise_url(url: str) -> str:
    """URL comparison tolerant of a trailing slash and host case."""
    return (url or "").strip().rstrip("/").casefold()


def iter_bookmarks(page_size: int = 100):
    cursor = None
    while True:
        params = {"limit": page_size}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(
            f"{KARAKEEP_URL}/api/v1/bookmarks",
            params=params,
            headers=_headers(),
            timeout=60,
        )
        r.raise_for_status()
        payload = r.json()
        bookmarks = payload.get("bookmarks", [])
        if not bookmarks:
            return
        yield bookmarks
        cursor = payload.get("nextCursor")
        if not cursor:
            return


def _bookmark_index() -> dict[str, dict]:
    """URL -> bookmark, built by paginating Karakeep once per worker.

    A per-post search would cost one call each; walking the whole base costs
    ~20 calls for a few thousand bookmarks.
    """
    global _bookmarks_by_url
    if _bookmarks_by_url is not None:
        return _bookmarks_by_url
    index: dict[str, dict] = {}
    for page in iter_bookmarks():
        for bm in page:
            url = (bm.get("content") or {}).get("url")
            if url and "instagram.com" in url:
                index[_normalise_url(url)] = bm
    _bookmarks_by_url = index
    return index


def find_existing_bookmark(media: MediaItem) -> dict | None:
    """Return the Karakeep bookmark for this post if it already exists."""
    url = f"https://www.instagram.com/p/{media.code}/"
    return _bookmark_index().get(_normalise_url(url))


def complete_existing(media: MediaItem, bm: dict) -> PushOutcome:
    """Fill an existing bookmark's missing assets (reconcile mode)."""
    gaps = asset_jobs(bm, media)
    if not gaps:
        return PushOutcome(status="skipped")
    assets = push_assets(bm["id"], gaps)
    return PushOutcome(status="completed", assets=assets)
