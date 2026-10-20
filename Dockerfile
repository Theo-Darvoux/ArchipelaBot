FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1 PYTHONUNBUFFERED=1

# Archipelago, to regenerate seeds for /track logique: every version players generate with, the spoiler picks one.
ARG ARCHIPELAGO_VERSIONS="0.6.7 0.6.8"
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential fonts-inter fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*
RUN for version in $ARCHIPELAGO_VERSIONS; do \
        git clone -q --depth 1 --branch "$version" https://github.com/ArchipelagoMW/Archipelago.git \
            "/opt/Archipelago/$version" \
        && cd "/opt/Archipelago/$version" && uv venv -q .venv \
        && for requirements in requirements.txt worlds/*/requirements.txt; do \
            VIRTUAL_ENV=.venv uv pip install -q "setuptools<81" -r "$requirements" || exit 1; done \
        && { .venv/bin/python -m compileall -q . || true; } \
        || exit 1; \
    done
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
