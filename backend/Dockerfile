FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace

RUN addgroup --system prism && adduser --system --ingroup prism prism

COPY backend/pyproject.toml backend/README.md ./backend/
COPY backend/src ./backend/src
COPY backend/alembic.ini backend/entrypoint.sh ./backend/
COPY config ./config
COPY migrations ./migrations

RUN python -m pip install --upgrade pip

FROM base AS test

COPY backend/tests ./backend/tests
RUN python -m pip install "./backend[dev]" \
    && chown -R prism:prism /workspace/backend

USER prism
WORKDIR /workspace/backend
CMD ["pytest", "--cov=prism", "--cov-report=term-missing"]

FROM base AS runtime

RUN python -m pip install ./backend \
    && chmod +x ./backend/entrypoint.sh

USER prism

WORKDIR /workspace/backend

EXPOSE 8000

CMD ["./entrypoint.sh"]

