"""Tests for the Karakeep asset pipeline (download, upload, attach, reconcile)."""

from __future__ import annotations

import requests

import app.sync.karakeep as karakeep_svc
from app.sync.karakeep import (
    asset_jobs,
    complete_existing,
    find_existing_bookmark,
    push_assets,
)
from app.sync.models import EnrichedBookmark, MediaItem, MediaResource


def _media(**kw) -> MediaItem:
    base = {"pk": "1", "code": "abc", "username": "chef", "caption": ""}
    base.update(kw)
    return MediaItem(**base)


def test_asset_jobs_new_bookmark_default_types() -> None:
    media = _media(
        thumbnail_url="https://cdn/thumb.jpg",
        media_type=2,
        video_url="https://cdn/v.mp4",
        resources=[
            MediaResource(thumbnail_url="https://cdn/1.jpg"),
            MediaResource(thumbnail_url="https://cdn/2.jpg"),
        ],
    )
    types = [j["type"] for j in asset_jobs(None, media)]
    # Default ASSET_TYPES = banner,screenshot,carousel (no video).
    assert types == ["bannerImage", "screenshot", "userUploaded", "userUploaded"]


def test_asset_jobs_skips_present_useruploaded_on_reconcile() -> None:
    media = _media(
        thumbnail_url="https://cdn/thumb.jpg",
        resources=[MediaResource(thumbnail_url="https://cdn/1.jpg")],
    )
    existing = {
        "id": "bm-1",
        "assets": [
            {"assetType": "bannerImage", "id": "old-banner"},
            {"assetType": "userUploaded", "id": "slide"},
        ],
    }
    jobs = asset_jobs(existing, media)
    # Carousel slide already present -> not re-uploaded; banner is replaced.
    assert not any(j["type"] == "userUploaded" for j in jobs)
    banner = next(j for j in jobs if j["type"] == "bannerImage")
    assert banner["replace"] == "old-banner"


def test_push_assets_counts_successful_jobs(monkeypatch) -> None:
    monkeypatch.setattr(karakeep_svc, "download_cdn", lambda url: b"data")
    monkeypatch.setattr(karakeep_svc, "upload_asset", lambda c, f, m: "asset-id")
    monkeypatch.setattr(karakeep_svc, "attach_asset", lambda b, a, t: True)
    monkeypatch.setattr(karakeep_svc, "replace_asset", lambda b, o, n: True)

    def job(kind, replace=None):
        return {
            "type": kind,
            "replace": replace,
            "url": "u",
            "filename": "f",
            "mime": "m",
        }

    jobs = [job("bannerImage"), job("screenshot", replace="old")]
    assert push_assets("bm-1", jobs) == {"bannerImage": 1, "screenshot": 1}


def test_push_assets_skips_failed_download(monkeypatch) -> None:
    monkeypatch.setattr(karakeep_svc, "download_cdn", lambda url: None)
    jobs = [
        {
            "type": "bannerImage",
            "replace": None,
            "url": "u",
            "filename": "f",
            "mime": "m",
        }
    ]
    assert push_assets("bm-1", jobs) == {}


def test_find_existing_bookmark_matches_by_url(monkeypatch) -> None:
    monkeypatch.setattr(karakeep_svc, "_bookmarks_by_url", None)

    def fake_iter(*a, **kw):
        yield [
            {"id": "bm-1", "content": {"url": "https://www.instagram.com/p/abc/"}},
            {"id": "bm-2", "content": {"url": "https://example.com/x"}},
        ]

    monkeypatch.setattr(karakeep_svc, "iter_bookmarks", fake_iter)

    found = find_existing_bookmark(_media(code="abc"))
    assert found is not None and found["id"] == "bm-1"
    assert find_existing_bookmark(_media(code="zzz")) is None


def test_complete_existing_skips_when_nothing_missing() -> None:
    media = _media()  # no thumbnail, no resources -> no asset jobs
    outcome = complete_existing(media, {"id": "bm-1", "assets": []})
    assert outcome.status == "skipped"


# --- Karakeep HTTP helpers ----------------------------------------------------


class _Resp:
    """Minimal requests.Response stand-in."""

    def __init__(self, status_code=200, body=None, headers=None, chunks=()) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}
        self._chunks = chunks

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.RequestException(f"status {self.status_code}")

    def json(self):
        return self._body

    def iter_content(self, size):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_download_cdn_streams_and_concatenates(monkeypatch) -> None:
    monkeypatch.setattr(
        karakeep_svc.requests, "get", lambda *a, **kw: _Resp(chunks=[b"ab", b"cd"])
    )
    assert karakeep_svc.download_cdn("https://cdn/x.jpg") == b"abcd"


def test_download_cdn_rejects_oversized_content_length(monkeypatch) -> None:
    huge = str(int(karakeep_svc.MAX_ASSET_MB * 1024 * 1024) + 1)
    monkeypatch.setattr(
        karakeep_svc.requests,
        "get",
        lambda *a, **kw: _Resp(headers={"Content-Length": huge}),
    )
    assert karakeep_svc.download_cdn("https://cdn/big.mp4") is None


def test_download_cdn_bounds_a_lying_content_length(monkeypatch) -> None:
    # No Content-Length, but the stream itself exceeds the cap.
    monkeypatch.setattr(karakeep_svc, "MAX_ASSET_MB", 0.000001)  # 1 byte
    monkeypatch.setattr(
        karakeep_svc.requests, "get", lambda *a, **kw: _Resp(chunks=[b"xxxx"])
    )
    assert karakeep_svc.download_cdn("https://cdn/x.jpg") is None


def test_download_cdn_network_error_returns_none(monkeypatch) -> None:
    def boom(*a, **kw):
        raise requests.RequestException("down")

    monkeypatch.setattr(karakeep_svc.requests, "get", boom)
    assert karakeep_svc.download_cdn("https://cdn/x.jpg") is None


def test_upload_asset_returns_id_and_handles_rejection(monkeypatch) -> None:
    monkeypatch.setattr(
        karakeep_svc.requests, "post", lambda *a, **kw: _Resp(201, {"assetId": "a-1"})
    )
    assert karakeep_svc.upload_asset(b"x", "f.jpg", "image/jpeg") == "a-1"

    monkeypatch.setattr(karakeep_svc.requests, "post", lambda *a, **kw: _Resp(500, {}))
    assert karakeep_svc.upload_asset(b"x", "f.jpg", "image/jpeg") is None


def test_upload_asset_network_error_returns_none(monkeypatch) -> None:
    def boom(*a, **kw):
        raise requests.RequestException("down")

    monkeypatch.setattr(karakeep_svc.requests, "post", boom)
    assert karakeep_svc.upload_asset(b"x", "f.jpg", "image/jpeg") is None


def test_attach_asset_status_handling(monkeypatch) -> None:
    monkeypatch.setattr(karakeep_svc.requests, "post", lambda *a, **kw: _Resp(201))
    assert karakeep_svc.attach_asset("bm-1", "a-1", "bannerImage") is True

    monkeypatch.setattr(karakeep_svc.requests, "post", lambda *a, **kw: _Resp(400))
    assert karakeep_svc.attach_asset("bm-1", "a-1", "bannerImage") is False


def test_attach_asset_network_error_returns_false(monkeypatch) -> None:
    def boom(*a, **kw):
        raise requests.RequestException("down")

    monkeypatch.setattr(karakeep_svc.requests, "post", boom)
    assert karakeep_svc.attach_asset("bm-1", "a-1", "bannerImage") is False


def test_replace_asset_accepts_204(monkeypatch) -> None:
    monkeypatch.setattr(karakeep_svc.requests, "put", lambda *a, **kw: _Resp(204))
    assert karakeep_svc.replace_asset("bm-1", "old", "new") is True

    def boom(*a, **kw):
        raise requests.RequestException("down")

    monkeypatch.setattr(karakeep_svc.requests, "put", boom)
    assert karakeep_svc.replace_asset("bm-1", "old", "new") is False


def test_push_assets_skips_failed_upload_and_attach(monkeypatch) -> None:
    monkeypatch.setattr(karakeep_svc, "download_cdn", lambda url: b"data")
    monkeypatch.setattr(karakeep_svc, "upload_asset", lambda c, f, m: None)
    job = {
        "type": "screenshot",
        "replace": None,
        "url": "u",
        "filename": "f",
        "mime": "m",
    }
    assert push_assets("bm-1", [job]) == {}

    monkeypatch.setattr(karakeep_svc, "upload_asset", lambda c, f, m: "a-1")
    monkeypatch.setattr(karakeep_svc, "attach_asset", lambda b, a, t: False)
    assert push_assets("bm-1", [job]) == {}


def test_asset_jobs_includes_video_and_avatar_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        karakeep_svc, "ASSET_TYPES", ["banner", "video", "carousel", "avatar"]
    )
    media = _media(
        thumbnail_url="https://cdn/thumb.jpg",
        media_type=2,
        video_url="https://cdn/v.mp4",
        profile_pic_url="https://cdn/me.jpg",
        resources=[MediaResource(video_url="https://cdn/slide.mp4")],
    )
    jobs = asset_jobs(None, media)
    assert [j["type"] for j in jobs] == [
        "bannerImage",
        "video",
        "userUploaded",
        "avatar",
    ]
    # The carousel slide is a video, so it keeps the mp4 mime type.
    assert jobs[2]["mime"] == "video/mp4"


def test_asset_jobs_keeps_banner_when_replace_disabled(monkeypatch) -> None:
    monkeypatch.setattr(karakeep_svc, "REPLACE_BANNER", False)
    monkeypatch.setattr(karakeep_svc, "ASSET_TYPES", ["banner"])
    media = _media(thumbnail_url="https://cdn/thumb.jpg")
    existing = {"id": "bm-1", "assets": [{"assetType": "bannerImage", "id": "old"}]}
    assert asset_jobs(existing, media) == []


def test_complete_existing_pushes_the_gaps(monkeypatch) -> None:
    monkeypatch.setattr(karakeep_svc, "ASSET_TYPES", ["banner"])
    monkeypatch.setattr(karakeep_svc, "push_assets", lambda bid, jobs: {"bannerImage": 1})
    media = _media(thumbnail_url="https://cdn/thumb.jpg")
    outcome = complete_existing(media, {"id": "bm-1", "assets": []})
    assert outcome.status == "completed"
    assert outcome.assets == {"bannerImage": 1}


def test_iter_bookmarks_follows_the_cursor(monkeypatch) -> None:
    pages = [
        _Resp(200, {"bookmarks": [{"id": "b1"}], "nextCursor": "c2"}),
        _Resp(200, {"bookmarks": [{"id": "b2"}]}),  # no cursor -> stop
    ]
    seen_params: list[dict] = []

    def fake_get(url, params=None, **kw):
        seen_params.append(params)
        return pages.pop(0)

    monkeypatch.setattr(karakeep_svc.requests, "get", fake_get)
    assert [p[0]["id"] for p in karakeep_svc.iter_bookmarks()] == ["b1", "b2"]
    assert seen_params[1]["cursor"] == "c2"


def test_iter_bookmarks_stops_on_empty_page(monkeypatch) -> None:
    monkeypatch.setattr(
        karakeep_svc.requests, "get", lambda *a, **kw: _Resp(200, {"bookmarks": []})
    )
    assert list(karakeep_svc.iter_bookmarks()) == []


# --- Lists --------------------------------------------------------------------


def test_create_list_returns_id_and_refreshes_cache(monkeypatch) -> None:
    monkeypatch.setattr(karakeep_svc, "_lists_cache", [])
    monkeypatch.setattr(
        karakeep_svc.requests, "post", lambda *a, **kw: _Resp(201, {"id": "l9"})
    )
    assert karakeep_svc.create_list("Voyage", parent_id="p1") == "l9"
    assert karakeep_svc._lists_cache == [{"id": "l9", "name": "Voyage"}]


def test_create_list_network_error_returns_none(monkeypatch) -> None:
    def boom(*a, **kw):
        raise requests.RequestException("down")

    monkeypatch.setattr(karakeep_svc.requests, "post", boom)
    assert karakeep_svc.create_list("Voyage") is None


def test_attach_tags_noop_on_empty_and_posts_otherwise(monkeypatch) -> None:
    assert karakeep_svc.attach_tags("bm-1", []) is True

    posted: list = []
    monkeypatch.setattr(
        karakeep_svc.requests,
        "post",
        lambda url, json=None, **kw: (posted.append(json), _Resp(201))[1],
    )
    assert karakeep_svc.attach_tags("bm-1", ["food"]) is True
    assert posted == [{"tags": [{"tagName": "food"}]}]


def test_attach_tags_network_error_returns_false(monkeypatch) -> None:
    def boom(*a, **kw):
        raise requests.RequestException("down")

    monkeypatch.setattr(karakeep_svc.requests, "post", boom)
    assert karakeep_svc.attach_tags("bm-1", ["food"]) is False


def test_resolve_list_abstains_when_ambiguous(monkeypatch) -> None:
    monkeypatch.setattr(karakeep_svc, "CREATE_MISSING_LISTS", False)
    index = {"voyage": ["l1", "l2"]}
    assert karakeep_svc._resolve_list("Voyage", index) is None
    assert karakeep_svc._resolve_list("Ghost", index) is None  # missing, creation off


def test_resolve_list_creates_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(karakeep_svc, "CREATE_MISSING_LISTS", True)
    monkeypatch.setattr(karakeep_svc, "create_list", lambda name: "l9")
    index: dict[str, list[str]] = {}
    assert karakeep_svc._resolve_list("Ghost", index) == "l9"
    assert index["ghost"] == ["l9"]


def test_target_lists_merges_collections_and_default(monkeypatch) -> None:
    monkeypatch.setattr(
        karakeep_svc,
        "_lists_cache",
        [{"id": "l1", "name": "Voyage"}, {"id": "l2", "name": "Cuisine"}],
    )
    monkeypatch.setattr(karakeep_svc, "KARAKEEP_LIST_ID", "l3")
    media = _media(collection_names=["Cuisine", "Voyage"])  # Voyage also from the LLM
    enrichment = EnrichedBookmark(title="t", note="n", tags=[], lists=["Voyage"])
    assert karakeep_svc._target_lists(media, enrichment) == [
        ("l1", "Voyage"),
        ("l2", "Cuisine"),
        ("l3", ""),
    ]
