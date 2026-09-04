"""Seen-state persistence (deduplication across sync runs)."""

from __future__ import annotations

import json

from app.config import STATE_FILE


def load_seen() -> list[str]:
    if not STATE_FILE.exists():
        return []
    try:
        return sorted(json.loads(STATE_FILE.read_text()))
    except json.JSONDecodeError:
        return []


def save_seen(seen: list[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(seen), indent=0))
