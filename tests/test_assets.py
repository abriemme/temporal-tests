"""Tests for the Karakeep asset pipeline (download, upload, attach, reconcile)."""

from __future__ import annotations

import app.sync.karakeep as karakeep_svc
from app.sync.karakeep import (
    asset_jobs,
    complete_existing,
    find_existing_bookmark,
    push_assets,
)
from app.sync.models import MediaItem, MediaResource


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
