# temporal-tests

Project goal: **sync Instagram saved posts to
[Karakeep](https://karakeep.app/)**, using Temporal for orchestration (retries,
scheduling, idempotence).

The project is the **actual sync** (`IgSyncWorkflow`): the port of the former
n8n-driven `ig_to_karakeep.py` script — Instagram saved posts -> Karakeep
bookmarks, orchestrated by Temporal (activities, retries, timers, dedup state).
Built in Python managed with [`uv`](https://docs.astral.sh/uv/):

- a **worker** using **Worker Deployment Versioning** with the **git SHA** as `build_id`;
- **activity unit tests** via `ActivityEnvironment` (heartbeats, cancellation);
- **workflow tests** via `WorkflowEnvironment.start_time_skipping()`, with
  **mocked activities**;
- a **replay test** based on JSON histories in `tests/histories/`;
- a **GitHub Actions workflow** that runs the tests + replay, then builds a **Docker image tagged with the SHA**.

## Layout

```
app/
  config.py            # all env-based settings (Temporal, Karakeep, DATA_DIR)
                       # + build_id resolution (git SHA)
  sync/
    models.py          # SyncInput/SyncParams/SyncSummary, MediaItem (no I/O)
    workflow.py        # IgSyncWorkflow: dedup, ordering, pacing, cooldown timer
    activities.py      # thin @activity.defn wrappers
    instagram.py       # instagrapi session + saved-posts fetch (service)
    karakeep.py        # bookmark creation, payload, list membership (service)
    state.py           # seen.json dedup state (service)
    __init__.py        # lazy re-exports (sandbox-safe)
  starter.py           # manual sync run + nightly Schedule creation (replaces n8n)
  worker.py            # Worker + WorkerDeploymentConfig (use_worker_versioning=True)
tests/
  conftest.py          # start_time_skipping() fixture
  test_activities.py   # service-layer unit tests + ActivityEnvironment mechanics
  test_sync_workflow.py# time-skipping tests (mocked activities)
  test_replay.py       # replays each tests/histories/*.json
  histories/           # committed JSON histories (generated)
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

## Instagram -> Karakeep sync

What replaced the n8n machinery:

| Old (n8n script) | New |
|---|---|
| HTTP endpoint + n8n cron | Temporal Schedule (`uv run python -m app.starter schedule`) or manual `... starter sync [--backfill]` |
| `cooldown.json` circuit breaker | `workflow.sleep(cooldown_hours)` timer when the activity raises a `ChallengeDetected` `ApplicationError` |
| manual retry/bool bookkeeping | activity `RetryPolicy` + timeouts |
| `time.sleep` pacing | `workflow.sleep` (deterministic, replayable) |

Still needed (unchanged): `instagrapi` session (`DATA_DIR/session.json`,
produced by the interactive login CLI — to be ported), dedup state
(`DATA_DIR/seen.json`), and the `ig` optional dependency group on the worker
machine: `uv sync --group ig`.

Environment: `KARAKEEP_URL`, `KARAKEEP_TOKEN`, `KARAKEEP_LIST_ID` (optional),
`MAX_ITEMS`, `DATA_DIR`.

## Worker Deployment Versioning

The worker (`app/worker.py`) registers under the `deployment_name`
`ig-to-karakeep` with a `build_id` = git SHA:

```python
WorkerDeploymentConfig(
    version=WorkerDeploymentVersion(deployment_name="ig-to-karakeep", build_id=<git-sha>),
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
docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t ig-to-karakeep:$(git rev-parse HEAD) .
```

The image installs dependencies from `uv.lock`. In CI it is tagged
`ghcr.io/<repo>:<sha>` (see `.github/workflows/ci.yml`).

## CI

`.github/workflows/ci.yml`:

1. **`test` job**: `uv sync --frozen` then `uv run pytest -v` (time-skipping + replay tests), with `GIT_SHA=github.sha`;
2. **`docker` job** (depends on `test`): builds the image and tags it `:<github.sha>`,
   pushing to GHCR on the default branch.
