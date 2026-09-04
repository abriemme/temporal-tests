# temporal-tests

Project goal: **sync Instagram saved posts to
[Karakeep](https://karakeep.app/)**, using Temporal for orchestration (retries,
scheduling, idempotence).

The current code is the **Temporal scaffolding** (example workflow/worker) that
demonstrates, in Python managed with [`uv`](https://docs.astral.sh/uv/):

- a **worker** using **Worker Deployment Versioning** with the **git SHA** as `build_id`;
- an example **workflow** using **`workflow.patched`**;
- **activity unit tests** via `ActivityEnvironment` (heartbeats, cancellation);
- **workflow tests** via `WorkflowEnvironment.start_time_skipping()`, including a
  variant with **mocked activities**;
- a **replay test** based on JSON histories in `tests/histories/`;
- a **GitHub Actions workflow** that runs the tests + replay, then builds a **Docker image tagged with the SHA**.

## Layout

```
app/
  config.py       # deployment_name, task queue, build_id resolution (git SHA)
  activities.py   # compose_greeting, shout
  workflows.py    # GreetingWorkflow (workflow.patched), SleepyGreetingWorkflow
  worker.py       # Worker + WorkerDeploymentConfig (use_worker_versioning=True)
tests/
  conftest.py     # start_time_skipping() fixture
  test_activities.py # ActivityEnvironment unit tests (heartbeat, cancellation)
  test_workflow.py# time-skipping tests + mocked-activities variant
  test_replay.py  # replays each tests/histories/*.json
  histories/      # committed JSON histories (generated)
scripts/
  generate_history.py  # (re)generates the histories
.github/workflows/ci.yml
Dockerfile
```

## Requirements

[`uv`](https://docs.astral.sh/uv/) and Python 3.14 (installed automatically by uv):

```bash
uv sync
```

This installs the dependencies (including the `dev` group: pytest,
pytest-asyncio) from `uv.lock`.

## Run the tests (unit + replay)

```bash
uv run pytest -v
```

Time-skipping and replay automatically download the bundled Temporal **test
server**: no infrastructure to start.

## (Re)generate the replay histories

After a *deliberate* and compatible workflow change (e.g. adding a patch),
regenerate the histories and commit them:

```bash
uv run python scripts/generate_history.py
```

The replay test (`test_replay.py`) replays each `tests/histories/*.json` against
the current code and fails if determinism is broken.

## Worker Deployment Versioning

The worker (`app/worker.py`) registers under the `deployment_name`
`greeting-app` with a `build_id` = git SHA:

```python
WorkerDeploymentConfig(
    version=WorkerDeploymentVersion(deployment_name="greeting-app", build_id=<git-sha>),
    use_worker_versioning=True,
    default_versioning_behavior=VersioningBehavior.PINNED,
)
```

Workflows declare `versioning_behavior=VersioningBehavior.PINNED`: each execution
stays pinned to the version that started it. To drive the traffic ramp between
versions, use the Temporal CLI/UI (`temporal worker deployment ...`).

Run a worker locally (requires a Temporal server, e.g. `temporal server start-dev`):

```bash
GIT_SHA=$(git rev-parse HEAD) uv run python -m app.worker
```

## Docker image

```bash
docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t greeting-app:$(git rev-parse HEAD) .
```

The image installs dependencies from `uv.lock`. In CI it is tagged
`ghcr.io/<repo>:<sha>` (see `.github/workflows/ci.yml`).

## CI

`.github/workflows/ci.yml`:

1. **`test` job**: `uv sync --frozen` then `uv run pytest -v` (time-skipping + replay tests), with `GIT_SHA=github.sha`;
2. **`docker` job** (depends on `test`): builds the image and tags it `:<github.sha>`,
   pushing to GHCR on the default branch.
