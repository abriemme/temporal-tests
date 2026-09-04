FROM python:3.14-slim

# uv to install dependencies from uv.lock (reproducible).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# The git SHA is passed at build time and baked into the image: the worker uses
# it as the build_id of its Worker Deployment version.
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependency layer, cached as long as pyproject.toml/uv.lock don't change.
# The "ig" group (instagrapi, pyotp) is needed at runtime: the worker executes
# the Instagram activities in-process.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --group ig

COPY app ./app

# Run the versioned worker.
CMD ["python", "-m", "app.worker"]
