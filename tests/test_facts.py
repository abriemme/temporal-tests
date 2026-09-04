"""Tests for the deterministic (LLM-free) note and tag derivation."""

from __future__ import annotations

from app.sync.facts import build_note, derive_tags, merge_tags, slug_tag
from app.sync.models import MediaItem


def _media(**kw) -> MediaItem:
    base = {"pk": "1", "code": "abc", "username": "chef", "caption": ""}
    base.update(kw)
    return MediaItem(**base)


def test_slug_tag_normalizes() -> None:
    assert slug_tag("  #Foo Bar  ") == "foo bar"
    assert slug_tag("@Handle") == "handle"


def test_derive_tags_from_metadata() -> None:
    media = _media(
        caption="Great dish #Food #Paris",
        username="chef",
        product_type="clips",
        music_artist="Daft Punk",
        location_city="Paris",
    )
    tags = derive_tags(media)
    assert "food" in tags
    assert "paris" in tags
    assert "chef" in tags  # author
    assert "reel" in tags  # product_type clips
    assert "daft punk" in tags  # music artist


def test_derive_tags_drops_hashtag_spam() -> None:
    caption = " ".join(f"#tag{i}" for i in range(30))
    tags = derive_tags(_media(caption=caption, username="x"))
    # 30 hashtags is an SEO caption: hashtags dropped, only the author remains.
    assert not any(t.startswith("tag") for t in tags)
    assert "x" in tags


def test_merge_tags_dedupes_and_caps() -> None:
    merged = merge_tags(["food", "paris"], ["Paris", "dessert"])
    assert merged == ["food", "paris", "dessert"]


def test_build_note_is_rich_and_ordered() -> None:
    media = _media(
        username="chef",
        full_name="The Chef",
        caption="A recipe",
        music_title="Song",
        music_artist="Artist",
        location_name="Rome",
        duration=42.4,
    )
    note = build_note(media)
    assert note.startswith("@chef (The Chef)")
    assert "Music: Artist — Song" in note
    assert "Place: Rome" in note
    assert "Duration: 42 s" in note
    assert note.endswith("A recipe")


def test_build_note_empty_when_no_facts() -> None:
    assert build_note(_media(username="")) == ""
