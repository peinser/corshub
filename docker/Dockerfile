# builder-base ################################################################

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder-base

LABEL artifact=false

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYSETUP_PATH="/opt/pysetup" \
    VENV_PATH="/opt/pysetup/.venv"

WORKDIR ${PYSETUP_PATH}
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev --no-editable

# validate ####################################################################

FROM builder-base AS validate

LABEL artifact=false

# The following is necessary for running the unit tests
# For now, OPA responses are mocked in the unit tests.
ENV OPA_URL=http://opa:8181

RUN uv sync --frozen && \
    uv run ruff format --check src/corshub tests && \
    uv run ruff check src/corshub tests && \
    uv run pytest --cov=src/corshub && \
    uv run bandit -r src --severity-level medium
# NOTE: mypy is intentionally not gated here yet — `src/corshub` currently has
# pre-existing strict-mode errors (see BACKLOG). Run `make lint` locally for the
# full type check; promote it to a CI gate once the error count reaches zero.

# production ##################################################################

FROM docker.io/python:3.14-slim AS production

LABEL artifact=true

ENV VENV_PATH="/app/.venv"
ENV PATH="/app/.venv/bin:$PATH"

RUN groupadd --gid 1001 app && \
    useradd --uid 1001 --gid app --shell /bin/bash --create-home app

WORKDIR /app

COPY --from=builder-base /opt/pysetup/.venv /app/.venv
COPY ./docker/entrypoint.sh ./entrypoint.sh

RUN chmod +x ./entrypoint.sh && \
    chown -R app:app /app

USER app

ENTRYPOINT ["./entrypoint.sh"]
