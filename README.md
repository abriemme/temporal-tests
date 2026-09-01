# temporal-tests

Projet Temporal (Python, géré avec [`uv`](https://docs.astral.sh/uv/)) qui illustre :

- un **worker** utilisant le **Worker Deployment Versioning** avec le **SHA git** comme `build_id` ;
- un **workflow** d'exemple avec **`workflow.patched`** ;
- des **tests** via `WorkflowEnvironment.start_time_skipping()` ;
- un **test de replay** basé sur des historiques JSON dans `tests/histories/` ;
- un **workflow GitHub Actions** qui lance les tests + le replay puis build une **image Docker taguée avec le SHA**.

## Structure

```
app/
  config.py       # deployment_name, task queue, résolution du build_id (SHA git)
  activities.py   # compose_greeting, shout
  workflows.py    # GreetingWorkflow (workflow.patched), SleepyGreetingWorkflow
  worker.py       # Worker + WorkerDeploymentConfig (use_worker_versioning=True)
tests/
  conftest.py     # fixture start_time_skipping()
  test_workflow.py# tests avec time-skipping
  test_replay.py  # replay de chaque tests/histories/*.json
  histories/      # historiques JSON commités (générés)
scripts/
  generate_history.py  # (re)génère les historiques
.github/workflows/ci.yml
Dockerfile
```

## Prérequis

[`uv`](https://docs.astral.sh/uv/) et Python 3.14 (installé automatiquement par uv) :

```bash
uv sync
```

Cela installe les dépendances (dont le groupe `dev` : pytest, pytest-asyncio)
depuis `uv.lock`.

## Lancer les tests (unitaires + replay)

```bash
uv run pytest -v
```

Le time-skipping et le replay téléchargent automatiquement le **test server**
Temporal intégré : aucune infrastructure à démarrer.

## (Re)générer les historiques de replay

Après une évolution *volontaire* et compatible du workflow (ex. ajout d'un patch),
régénérez les historiques et committez-les :

```bash
uv run python scripts/generate_history.py
```

Le test de replay (`test_replay.py`) rejoue chaque `tests/histories/*.json`
contre le code actuel et échoue si le déterminisme est cassé.

## Worker Deployment Versioning

Le worker (`app/worker.py`) s'enregistre sous le `deployment_name`
`greeting-app` avec un `build_id` = SHA git :

```python
WorkerDeploymentConfig(
    version=WorkerDeploymentVersion(deployment_name="greeting-app", build_id=<git-sha>),
    use_worker_versioning=True,
    default_versioning_behavior=VersioningBehavior.PINNED,
)
```

Les workflows déclarent `versioning_behavior=VersioningBehavior.PINNED` : chaque
exécution reste épinglée à la version qui l'a démarrée. Pour piloter la rampe de
trafic entre versions, utilisez le CLI/l'UI Temporal (`temporal worker deployment ...`).

Lancer un worker en local (nécessite un serveur Temporal, ex. `temporal server start-dev`) :

```bash
GIT_SHA=$(git rev-parse HEAD) uv run python -m app.worker
```

## Image Docker

```bash
docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t greeting-app:$(git rev-parse HEAD) .
```

L'image installe les dépendances depuis `uv.lock`. En CI, elle est taguée
`ghcr.io/<repo>:<sha>` (voir `.github/workflows/ci.yml`).

## CI

`.github/workflows/ci.yml` :

1. **job `test`** : `uv sync --frozen` puis `uv run pytest -v` (tests time-skipping + replay), avec `GIT_SHA=github.sha` ;
2. **job `docker`** (dépend de `test`) : build de l'image et tag `:<github.sha>`,
   poussée sur GHCR sur la branche par défaut.
