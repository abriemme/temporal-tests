"""Test de replay basé sur des historiques JSON commités.

Le replay rejoue un historique d'exécution réel contre le code *actuel* des
workflows. Si une modification casse le déterminisme (suppression d'une commande,
réordonnancement, activity retirée sans ``workflow.patched``...), le replay lève
une ``NondeterminismError`` et le test échoue — avant tout déploiement.

Les historiques vivent dans ``tests/histories/*.json`` (format JSON export
Temporal, celui de ``temporal workflow show --output json`` ou de
``WorkflowHistory.to_json``). Voir ``scripts/generate_history.py`` pour les
(re)générer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from app.workflows import GreetingWorkflow, SleepyGreetingWorkflow

HISTORIES_DIR = Path(__file__).parent / "histories"


def _history_files() -> list[Path]:
    return sorted(HISTORIES_DIR.glob("*.json"))


@pytest.mark.parametrize(
    "history_path",
    _history_files(),
    ids=lambda p: p.name,
)
@pytest.mark.asyncio
async def test_replay_history(history_path: Path) -> None:
    history = WorkflowHistory.from_json(
        # L'id du workflow n'a pas d'importance pour le replay ; on prend le nom
        # de fichier pour des messages d'erreur lisibles.
        workflow_id=history_path.stem,
        history=json.loads(history_path.read_text()),
    )

    replayer = Replayer(workflows=[GreetingWorkflow, SleepyGreetingWorkflow])
    # Lève NondeterminismError si le code actuel diverge de l'historique.
    await replayer.replay_workflow(history)


def test_histories_present() -> None:
    """Garde-fou : au moins un historique doit être commité."""
    assert _history_files(), (
        "Aucun historique dans tests/histories/. "
        "Lancez `python scripts/generate_history.py` pour en générer."
    )
