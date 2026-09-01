FROM python:3.14-slim

# uv pour installer les dépendances depuis uv.lock (reproductible).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Le SHA git est passé au build et figé dans l'image : le worker l'utilise
# comme build_id de sa version de Worker Deployment.
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Couche de dépendances mise en cache tant que pyproject.toml/uv.lock ne changent pas.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app

# Lance le worker versionné.
CMD ["python", "-m", "app.worker"]
