"""Worker Temporal utilisant le Worker Deployment Versioning.

Le ``build_id`` provient du SHA git (cf. ``app.config.get_build_id``). Chaque
build/déploiement expose donc une nouvelle *version* de worker sous le même
``deployment_name``, ce qui permet des rolling deployments contrôlés côté
serveur (rampe de trafic, rollback, etc.).
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.common import VersioningBehavior
from temporalio.worker import (
    Worker,
    WorkerDeploymentConfig,
    WorkerDeploymentVersion,
)

from app.activities import compose_greeting, shout
from app.config import (
    DEPLOYMENT_NAME,
    TASK_QUEUE,
    TEMPORAL_ADDRESS,
    TEMPORAL_NAMESPACE,
    get_build_id,
)
from app.workflows import GreetingWorkflow, SleepyGreetingWorkflow


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    build_id = get_build_id()
    logging.info(
        "Starting worker deployment=%s build_id=%s task_queue=%s",
        DEPLOYMENT_NAME,
        build_id,
        TASK_QUEUE,
    )

    client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[GreetingWorkflow, SleepyGreetingWorkflow],
        activities=[compose_greeting, shout],
        deployment_config=WorkerDeploymentConfig(
            version=WorkerDeploymentVersion(
                deployment_name=DEPLOYMENT_NAME,
                build_id=build_id,
            ),
            # Active le Worker Deployment Versioning : le serveur route les
            # tâches vers la bonne version en fonction du comportement déclaré.
            use_worker_versioning=True,
            # Filet de sécurité pour les workflows qui ne déclarent pas
            # explicitement leur ``versioning_behavior``.
            default_versioning_behavior=VersioningBehavior.PINNED,
        ),
    )

    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
