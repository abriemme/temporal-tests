"""Tests for the one-off maintenance utilities (retag, collections)."""

from __future__ import annotations

import app.sync.instagram as instagram_svc
import app.sync.karakeep as karakeep_svc
from app.sync.maintenance import reconcile_collections, retag, tags_from_bookmark


def test_tags_from_bookmark_uses_title_and_note() -> None:
    bm = {"title": "@chef — A dish", "note": "yum #Food #Paris"}
    assert tags_from_bookmark(bm) == ["chef", "food", "paris"]


def test_tags_from_bookmark_drops_hashtag_spam() -> None:
    note = " ".join(f"#tag{i}" for i in range(30))
    tags = tags_from_bookmark({"title": "@x", "note": note})
    assert tags == ["x"]  # author kept, spammy hashtags dropped


def _page(*bookmarks):
    def fake_iter(*a, **kw):
        yield list(bookmarks)

    return fake_iter


def test_retag_dry_run_counts_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        karakeep_svc,
        "iter_bookmarks",
        _page(
            {
                "id": "b1",
                "content": {"url": "https://www.instagram.com/p/x/"},
                "title": "@chef",
                "note": "#food",
                "tags": [],
            },
            {
                "id": "b2",
                "content": {"url": "https://example.com/x"},
                "title": "",
                "note": "",
                "tags": [],
            },
        ),
    )

    summary = retag(apply_changes=False)
    assert summary["scanned"] == 2
    assert summary["candidates"] == 1  # only the Instagram bookmark, missing tags
    assert summary["tagged"] == 0


def test_retag_apply_attaches_tags(monkeypatch) -> None:
    monkeypatch.setattr(
        karakeep_svc,
        "iter_bookmarks",
        _page(
            {
                "id": "b1",
                "content": {"url": "https://www.instagram.com/p/x/"},
                "title": "@chef",
                "note": "#food",
                "tags": [{"name": "chef"}],  # author already present
            }
        ),
    )
    attached: list = []
    monkeypatch.setattr(
        karakeep_svc,
        "attach_tags",
        lambda bid, tags: attached.append((bid, tags)) or True,
    )

    summary = retag(apply_changes=True)
    assert summary["tagged"] == 1
    assert attached == [("b1", ["food"])]  # only the missing tag


class _Col:
    def __init__(self, cid: str, name: str) -> None:
        self.id = cid
        self.name = name


class _FakeClient:
    def collections(self):
        return [
            _Col(instagram_svc.SAVED_COLLECTION, "All"),
            _Col("1", "Voyage"),
            _Col("2", "Ghost"),
        ]


def test_reconcile_collections_classifies(monkeypatch) -> None:
    monkeypatch.setattr(instagram_svc, "get_client", lambda: _FakeClient())
    monkeypatch.setattr(karakeep_svc, "list_index", lambda: {"voyage": ["l1"]})

    summary = reconcile_collections(apply_changes=False)
    assert summary["present"] == ["Voyage"]
    assert summary["missing"] == ["Ghost"]
    assert summary["created"] == 0


def test_reconcile_collections_creates_missing(monkeypatch) -> None:
    monkeypatch.setattr(instagram_svc, "get_client", lambda: _FakeClient())
    monkeypatch.setattr(karakeep_svc, "list_index", lambda: {"voyage": ["l1"]})
    monkeypatch.setattr(karakeep_svc, "create_list", lambda name, parent_id=None: "new")

    summary = reconcile_collections(apply_changes=True)
    assert summary["created"] == 1
