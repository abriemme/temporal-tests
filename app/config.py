"""Configuration shared between the worker, the workflows and the tests.

The ``build_id`` is derived from the git SHA of the current commit. In CI, the
``GIT_SHA`` environment variable is injected by GitHub Actions (``github.sha``).
Locally, we fall back to ``git rev-parse HEAD``.
"""

from __future__ import annotations

import os
import subprocess

# Logical name of the worker deployment. It must stay stable over time: it is
# the identity under which all versions (build_ids) succeed one another.
DEPLOYMENT_NAME = "greeting-app"

# Task queue used by the worker and the clients.
TASK_QUEUE = "greeting-task-queue"

# Temporal server address (overridden in prod via the environment).
TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
TEMPORAL_NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "default")


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
