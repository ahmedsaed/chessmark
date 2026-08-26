# The backend image: API, worker, and the operational commands.
#
# One image, three roles. They share every line of code — the worker is the API's own turn loop,
# and the tournament runner drives the same orchestration — so building three images would mean
# three chances for them to drift apart at exactly the point where they must not.
#
# `scripts/` is copied in because it *is* the operational interface now: starting a tournament,
# refreshing the catalogue, granting credits. Compose sets an entrypoint per service so those read
# as `docker compose run --rm tournament create --slug x` rather than a path into the image.

FROM python:3.12-slim AS base

# uv, pinned by digest-free tag but installed from the official image so the version is whatever
# that tag carries — matching how the project installs it locally.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app/apps/api

# Dependencies first, as their own layer: they change far less often than the source, and a code
# edit should not re-resolve the whole lockfile.
# `README.md` comes along because `pyproject.toml` declares it, and hatchling validates that the
# file exists before it will build the package — a metadata field, not documentation, as far as
# the build is concerned.
COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY apps/api/src ./src
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini ./alembic.ini
RUN uv sync --frozen --no-dev

# The operational commands. They add `apps/api/src` to `sys.path` for local use; here the package
# is installed properly and that entry simply does not exist, which Python ignores.
COPY scripts /app/scripts

# Nothing here runs as root. A container that can write to its own source is a container where a
# compromise becomes a persistent one.
RUN useradd --create-home --uid 10001 chessmark \
    && chown -R chessmark:chessmark /app /opt/venv
USER chessmark

EXPOSE 8000

# Overridden per service in compose. The API is the default because it is the one thing that must
# answer on a port.
CMD ["uvicorn", "chessmark.main:app", "--host", "0.0.0.0", "--port", "8000"]
