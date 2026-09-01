"""Workflows du projet d'exemple.

Le workflow illustre deux mécanismes clés de Temporal :

* ``workflow.patched`` : permet de faire évoluer le code d'un workflow tout en
  gardant compatibles les exécutions déjà en cours (et les historiques rejoués).
* ``versioning_behavior`` : déclare comment ce workflow doit se comporter vis à
  vis du Worker Deployment Versioning. ``PINNED`` épingle chaque exécution à la
  version (build_id) qui l'a démarrée, ce qui est le choix sûr par défaut.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy, VersioningBehavior

# Les imports du "monde extérieur" (activities) passent par le passthrough du
# sandbox : ils sont importés ici pour partager les types/refs d'activities.
with workflow.unsafe.imports_passed_through():
    from app.activities import GreetingInput, compose_greeting, shout

# Identifiant du patch. Il doit rester stable : il est écrit dans l'historique
# sous forme d'un marqueur, et relu lors du replay.
_SHOUT_PATCH = "greeting-shout-v2"


@workflow.defn(versioning_behavior=VersioningBehavior.PINNED)
class GreetingWorkflow:
    @workflow.run
    async def run(self, name: str) -> str:
        greeting = await workflow.execute_activity(
            compose_greeting,
            GreetingInput(name),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        # Évolution de comportement introduite après coup, protégée par un patch.
        #
        # * Nouveau code (marqueur présent, ou première exécution) : on met le
        #   message en majuscules via une seconde activity.
        # * Ancien historique (pas de marqueur au moment du replay) :
        #   ``workflow.patched`` renvoie False -> on garde l'ancien comportement,
        #   ce qui préserve le déterminisme du replay.
        if workflow.patched(_SHOUT_PATCH):
            greeting = await workflow.execute_activity(
                shout,
                greeting,
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

        return greeting


@workflow.defn(versioning_behavior=VersioningBehavior.PINNED)
class SleepyGreetingWorkflow:
    """Variante avec un ``sleep`` : utile pour montrer le time-skipping en test."""

    @workflow.run
    async def run(self, name: str) -> str:
        # Un timer d'un jour : sans time-skipping, un test l'attendrait vraiment.
        await workflow.sleep(timedelta(days=1))
        return await workflow.execute_activity(
            compose_greeting,
            GreetingInput(name),
            start_to_close_timeout=timedelta(seconds=10),
        )
