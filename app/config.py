"""Configuration shared between the worker, the workflows and the tests.

The ``build_id`` is derived from the git SHA of the current commit. In CI, the
``GIT_SHA`` environment variable is injected by GitHub Actions (``github.sha``).
Locally, we fall back to ``git rev-parse HEAD``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Logical name of the worker deployment. It must stay stable over time: it is
# the identity under which all versions (build_ids) succeed one another.
DEPLOYMENT_NAME = "ig-to-karakeep"

# Task queue used by the worker and the clients.
TASK_QUEUE = "ig-sync-task-queue"

# Temporal server address (overridden in prod via the environment).
TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
TEMPORAL_NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "default")

# --- Sync (Instagram -> Karakeep) configuration ------------------------------

KARAKEEP_URL = os.environ.get("KARAKEEP_URL", "").rstrip("/")
KARAKEEP_TOKEN = os.environ.get("KARAKEEP_TOKEN", "")
KARAKEEP_LIST_ID = os.environ.get("KARAKEEP_LIST_ID", "").strip()

# How many of the most recent saved posts to examine per run; dedup does the
# rest. Set backfill=True on the workflow input to fetch the whole history.
MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "40"))

# instagrapi session and dedup state. On the NUC (Docker) this is /data.
DATA_DIR = Path(os.environ.get("DATA_DIR") or "./data")
SESSION_FILE = DATA_DIR / "session.json"
STATE_FILE = DATA_DIR / "seen.json"


def get_build_id() -> str:
    """Return the git SHA to use as the ``build_id`` of the worker version.

    Resolution order:
    1. ``GIT_SHA`` (injected by CI);
    2. ``git rev-parse HEAD`` locally;
    3. ``"unknown"`` as a last resort (no repo available).
    """
    sha = os.environ.get("GIT_SHA")
    if sha:
        return sha.strip()

    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
        return sha.decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
