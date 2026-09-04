"""Tests for Instagram collection-membership resolution and its cache."""

from __future__ import annotations

import json
import time

import app.sync.instagram as instagram_svc
from app.sync.instagram import (
    SAVED_COLLECTION,
    _annotate_collections,
    _collections_by_media_map,
    _load_collection_cache,
    _names_by_id,
    _save_collection_cache,
    inline_collection_ids,
)
from app.sync.models import MediaItem


def _media(pk: str) -> MediaItem:
    return MediaItem(pk=pk, code=f"c{pk}", username="chef", caption="")


class _Col:
    def __init__(self, cid: str, name: str) -> None:
        self.id = cid
        self.name = name


class _Media:
    def __init__(self, pk: str) -> None:
        self.pk = pk


class _FakeClient:
    """Client exposing collections + per-collection medias, no inline payload."""

    def __init__(self, last_json=None) -> None:
        self.last_json = last_json
        self.scanned: list[str] = []

    def collections(self):
        return [_Col(SAVED_COLLECTION, "All"), _Col("7", "Voyage")]

    def collection_medias(self, collection_id, amount):
        self.scanned.append(collection_id)
        return [_Media("1")]


def _reset(monkeypatch, tmp_path):
    """Isolate the per-process caches and the on-disk cache file."""
    monkeypatch.setattr(instagram_svc, "_collection_names_by_id", None)
    monkeypatch.setattr(instagram_svc, "_collections_by_media", None)
    monkeypatch.setattr(
        instagram_svc, "COLLECTION_CACHE_FILE", tmp_path / "collections.json"
    )


# --- Inline membership (free: already in the feed payload) --------------------


def test_inline_collection_ids_reads_last_json() -> None:
    cl = _FakeClient(
        last_json={
            "items": [
                {"media": {"pk": 1, "saved_collection_ids": [7]}},
                {"id": "2_99", "saved_collection_ids": ["7"]},  # pk from "id"
                {"media": {"pk": 3}},  # no collection -> absent
                "not-a-dict",
            ]
        }
    )
    assert inline_collection_ids(cl) == {"1": ["7"], "2": ["7"]}


def test_inline_collection_ids_without_payload() -> None:
    assert inline_collection_ids(_FakeClient()) == {}


def test_names_by_id_skips_the_saved_collection(monkeypatch, tmp_path) -> None:
    _reset(monkeypatch, tmp_path)
    assert _names_by_id(_FakeClient()) == {"7": "Voyage"}


def test_names_by_id_is_cached_and_degrades(monkeypatch, tmp_path) -> None:
    _reset(monkeypatch, tmp_path)

    class Broken:
        def collections(self):
            raise RuntimeError("private API down")

    assert _names_by_id(Broken()) == {}
    # Second call is served from the per-process cache, not the client.
    assert _names_by_id(Broken()) == {}


def test_annotate_collections_uses_inline_ids(monkeypatch, tmp_path) -> None:
    _reset(monkeypatch, tmp_path)
    cl = _FakeClient(
        last_json={"items": [{"media": {"pk": 1, "saved_collection_ids": [7]}}]}
    )
    items = [_media("1"), _media("2")]

    _annotate_collections(cl, items)

    assert items[0].collection_names == ["Voyage"]
    assert items[1].collection_names == []
    assert cl.scanned == []  # the expensive per-collection scan is not needed


def test_annotate_collections_is_a_noop_when_disabled(monkeypatch, tmp_path) -> None:
    _reset(monkeypatch, tmp_path)
    monkeypatch.setattr(instagram_svc, "SYNC_COLLECTIONS", False)
    items = [_media("1")]
    _annotate_collections(_FakeClient(), items)
    assert items[0].collection_names == []


# --- Fallback scan + disk cache -----------------------------------------------


def test_collections_by_media_map_scans_and_caches(monkeypatch, tmp_path) -> None:
    _reset(monkeypatch, tmp_path)
    cl = _FakeClient()

    assert _collections_by_media_map(cl) == {"1": {"Voyage"}}
    assert cl.scanned == ["7"]  # the saved collection itself is skipped
    # Written to disk for the next worker.
    assert json.loads((tmp_path / "collections.json").read_text())["map"] == {
        "1": ["Voyage"]
    }


def test_collections_by_media_map_degrades_without_collections(
    monkeypatch, tmp_path
) -> None:
    _reset(monkeypatch, tmp_path)

    class Broken:
        def collections(self):
            raise RuntimeError("private API down")

    assert _collections_by_media_map(Broken()) == {}


def test_collections_by_media_map_skips_unreadable_collection(
    monkeypatch, tmp_path
) -> None:
    _reset(monkeypatch, tmp_path)

    class PartlyBroken(_FakeClient):
        def collection_medias(self, collection_id, amount):
            raise RuntimeError("rate limited")

    assert _collections_by_media_map(PartlyBroken()) == {}


def test_collection_cache_roundtrip(monkeypatch, tmp_path) -> None:
    _reset(monkeypatch, tmp_path)
    _save_collection_cache({"1": {"Voyage"}})
    assert _load_collection_cache() == {"1": {"Voyage"}}


def test_collection_cache_ignored_when_stale(monkeypatch, tmp_path) -> None:
    _reset(monkeypatch, tmp_path)
    (tmp_path / "collections.json").write_text(
        json.dumps({"fetched_at": time.time() - 10_000_000, "map": {"1": ["Voyage"]}})
    )
    assert _load_collection_cache() is None


def test_collection_cache_ignored_when_corrupt_or_disabled(monkeypatch, tmp_path) -> None:
    _reset(monkeypatch, tmp_path)
    assert _load_collection_cache() is None  # no file yet

    (tmp_path / "collections.json").write_text("not json")
    assert _load_collection_cache() is None

    monkeypatch.setattr(instagram_svc, "COLLECTION_CACHE_HOURS", 0)
    assert _load_collection_cache() is None


def test_collections_by_media_map_prefers_the_cache(monkeypatch, tmp_path) -> None:
    _reset(monkeypatch, tmp_path)
    _save_collection_cache({"42": {"Cached"}})
    cl = _FakeClient()

    assert _collections_by_media_map(cl) == {"42": {"Cached"}}
    assert cl.scanned == []  # nothing re-scanned
