"""Configuration partagée entre le worker, les workflows et les tests.

Le `build_id` est dérivé du SHA git du commit courant. En CI, la variable
d'environnement ``GIT_SHA`` est injectée par GitHub Actions (``github.sha``).
En local, on retombe sur ``git rev-parse HEAD``.
"""

from __future__ import annotations

import os
import subprocess

# Nom logique du déploiement de workers. Il doit rester stable dans le temps :
# c'est l'identité sous laquelle toutes les versions (build_id) se succèdent.
DEPLOYMENT_NAME = "greeting-app"

# Task queue utilisée par le worker et les clients.
TASK_QUEUE = "greeting-task-queue"

# Adresse du serveur Temporal (surchargée en prod via l'environnement).
TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
TEMPORAL_NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "default")


def get_build_id() -> str:
    """Retourne le SHA git à utiliser comme ``build_id`` de la version de worker.

    Ordre de résolution :
    1. ``GIT_SHA`` (injecté par la CI) ;
    2. ``git rev-parse HEAD`` en local ;
    3. ``"unknown"`` en dernier recours (repo absent).
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
