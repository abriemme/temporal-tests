"""Instagram -> Karakeep sync package.

Public surface: workflow, activities and models, re-exported lazily
(``from app.sync import IgSyncWorkflow, ...``). Lazy re-exports keep the
package importable from inside the workflow sandbox: importing ``app.sync``
must not pull I/O modules (requests, instagrapi) eagerly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - static analysis only
    from app.sync.activities import fetch_saved, load_seen, push_to_karakeep, save_seen
    from app.sync.models import (
        CHALLENGE_ERROR_TYPE,
        MediaItem,
        SyncInput,
        SyncParams,
        SyncSummary,
    )
    from app.sync.workflow import PUSH_DELAY, IgSyncWorkflow

_LAZY_EXPORTS = {
    "CHALLENGE_ERROR_TYPE": "app.sync.models",
    "IgSyncWorkflow": "app.sync.workflow",
    "MediaItem": "app.sync.models",
    "PUSH_DELAY": "app.sync.workflow",
    "SyncInput": "app.sync.models",
    "SyncParams": "app.sync.models",
    "SyncSummary": "app.sync.models",
    "fetch_saved": "app.sync.activities",
    "load_seen": "app.sync.activities",
    "push_to_karakeep": "app.sync.activities",
    "save_seen": "app.sync.activities",
}


def __getattr__(name: str):
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    import importlib

    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value  # cache for subsequent accesses
    return value


__all__ = sorted(_LAZY_EXPORTS)
