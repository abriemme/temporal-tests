"""Workflows for the example project.

The workflow illustrates two key Temporal mechanisms:

* ``workflow.patched``: lets you evolve a workflow's code while keeping
  in-flight executions (and replayed histories) compatible.
* ``versioning_behavior``: declares how this workflow should behave with respect
  to Worker Deployment Versioning. ``PINNED`` pins each execution to the version
  (build_id) that started it, which is the safe default choice.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy, VersioningBehavior

# Imports from the "outside world" (activities) go through the sandbox
# passthrough: they are imported here to share the activity types/refs.
with workflow.unsafe.imports_passed_through():
    from app.activities import GreetingInput, compose_greeting, shout

# Patch identifier. It must stay stable: it is written into the history as a
# marker and read back during replay.
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

        # Behavior change introduced after the fact, guarded by a patch.
        #
        # * New code (marker present, or first execution): uppercase the message
        #   via a second activity.
        # * Old history (no marker at replay time): ``workflow.patched`` returns
        #   False -> we keep the old behavior, which preserves replay
        #   determinism.
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
    """Variant with a ``sleep``: useful to demonstrate time-skipping in tests."""

    @workflow.run
    async def run(self, name: str) -> str:
        # A one-day timer: without time-skipping, a test would really wait for it.
        await workflow.sleep(timedelta(days=1))
        return await workflow.execute_activity(
            compose_greeting,
            GreetingInput(name),
            start_to_close_timeout=timedelta(seconds=10),
        )
