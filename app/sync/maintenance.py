"""One-off maintenance utilities (no Temporal workflow, run by hand).

- ``retag``: repair tags on Instagram bookmarks already imported into Karakeep,
  re-derived from what Karakeep already stores (title + note) — no Instagram
  call, so no risk to the account.
- ``collections``: reconcile Instagram collections with Karakeep lists of the
  same name; create the missing ones on ``--apply``.

Run with ``python -m app.sync.maintenance <command> [--apply] [--parent ID]``.
"""

from __future__ import annotations

import logging
import sys

from app.config import AUTO_TAGS, HASHTAG_SPAM_THRESHOLD, MAX_TAGS
from app.sync import karakeep
from app.sync.facts import AUTHOR_RE, HASHTAG_RE, slug_tag

log = logging.getLogger("ig2kk.maintenance")


def tags_from_bookmark(bm: dict) -> list[str]:
    """Re-derive tags from what Karakeep already stores (title + note).

    The title carries ``@author``, the note carries the caption's hashtags:
    everything needed is there, with no call to Instagram.
    """
    tags: list[str] = []

    def add(value: str | None) -> None:
        if not value:
            return
        tag = slug_tag(value)
        if tag and tag not in tags:
            tags.append(tag)

    title = bm.get("title") or ""
    note = bm.get("note") or ""

    if "author" in AUTO_TAGS:
        match = AUTHOR_RE.match(title.strip())
        if match:
            add(match.group(1))

    if "hashtags" in AUTO_TAGS and note:
        found = HASHTAG_RE.findall(note)
        if len(found) <= HASHTAG_SPAM_THRESHOLD:
            for tag in found:
                add(tag)

    return tags[:MAX_TAGS]


def _existing_tag_names(bm: dict) -> set[str]:
    return {(t.get("name") or "").casefold() for t in (bm.get("tags") or [])}


def retag(apply_changes: bool) -> dict:
    """Repair missing tags on already-imported Instagram bookmarks."""
    scanned = candidates = tagged = failed = 0

    for page in karakeep.iter_bookmarks():
        for bm in page:
            scanned += 1
            url = (bm.get("content") or {}).get("url") or ""
            if "instagram.com" not in url:
                continue

            existing = _existing_tag_names(bm)
            wanted = [t for t in tags_from_bookmark(bm) if t not in existing]
            if not wanted:
                continue

            candidates += 1
            if not apply_changes:
                continue

            if karakeep.attach_tags(bm["id"], wanted):
                tagged += 1
            else:
                failed += 1

    summary = {
        "scanned": scanned,
        "candidates": candidates,
        "tagged": tagged,
        "failed": failed,
        "applied": apply_changes,
    }
    log.info("retag: %s", summary)
    return summary


def reconcile_collections(apply_changes: bool, parent_id: str | None = None) -> dict:
    """Reconcile Instagram collections with same-named Karakeep lists."""
    from app.sync import instagram

    cl = instagram.get_client()
    try:
        collections = cl.collections()
    except Exception as exc:  # pragma: no cover - network path
        raise RuntimeError(f"Collections unreadable: {exc}") from exc

    index = karakeep.list_index()
    present, ambiguous, missing = [], [], []
    for col in collections:
        if str(col.id) == instagram.SAVED_COLLECTION:
            continue
        ids = index.get(karakeep.normalise(col.name), [])
        bucket = present if len(ids) == 1 else ambiguous if len(ids) > 1 else missing
        bucket.append(col.name)

    created = 0
    if apply_changes:
        for name in missing:
            if karakeep.create_list(name, parent_id=parent_id):
                created += 1

    summary = {
        "present": present,
        "ambiguous": ambiguous,
        "missing": missing,
        "created": created,
        "applied": apply_changes,
    }
    log.info(
        "collections: %d present, %d ambiguous, %d missing, %d created",
        len(present),
        len(ambiguous),
        len(missing),
        created,
    )
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = sys.argv[1:]
    command = args[0] if args else ""
    apply_changes = "--apply" in args

    if command == "retag":
        retag(apply_changes)
    elif command == "collections":
        parent = args[args.index("--parent") + 1] if "--parent" in args else None
        reconcile_collections(apply_changes, parent_id=parent)
    else:
        sys.exit("usage: python -m app.sync.maintenance {retag|collections} [--apply]")


if __name__ == "__main__":
    main()
