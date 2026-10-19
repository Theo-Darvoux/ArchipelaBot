FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1 PYTHONUNBUFFERED=1

# Archipelago, to regenerate seeds for /track logique: same version as the one players generate with.
ARG ARCHIPELAGO_VERSION=0.6.8
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "$ARCHIPELAGO_VERSION" https://github.com/ArchipelagoMW/Archipelago.git /opt/Archipelago \
    && cd /opt/Archipelago && uv venv -q .venv \
    && for requirements in requirements.txt worlds/*/requirements.txt; do \
        VIRTUAL_ENV=.venv uv pip install -q "setuptools<81" -r "$requirements" || exit 1; done \
    && { .venv/bin/python -m compileall -q . || true; }
ENV ARCHIPELAGO_PATH=/opt/Archipelago

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev

RUN useradd --create-home --uid 1000 archipelabot && mkdir /data && chown archipelabot /data
USER archipelabot

ENV DATABASE_PATH=/data/archipelabot.db
VOLUME /data

CMD ["/app/.venv/bin/archipelabot"]
