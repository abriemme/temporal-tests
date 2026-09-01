"""Temporal worker using Worker Deployment Versioning.

The ``build_id`` comes from the git SHA (see ``app.config.get_build_id``). Each
build/deployment therefore exposes a new worker *version* under the same
``deployment_name``, which enables controlled rolling deployments on the server
side (traffic ramp, rollback, etc.).
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
            # Enable Worker Deployment Versioning: the server routes tasks to the
            # right version based on the declared behavior.
            use_worker_versioning=True,
            # Safety net for workflows that don't explicitly declare their
            # ``versioning_behavior``.
            default_versioning_behavior=VersioningBehavior.PINNED,
        ),
    )

    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
