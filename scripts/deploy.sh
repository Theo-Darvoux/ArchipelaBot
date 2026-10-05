#!/usr/bin/env bash
set -euo pipefail
host="${1:?usage: scripts/deploy.sh user@host [directory]}"
dir="${2:-archipelabot}"
cd "$(dirname "$0")/.."

rsync -az --delete \
  --exclude .venv --exclude .dev --exclude data --exclude .env --exclude .git \
  --exclude .pytest_cache --exclude .ruff_cache --exclude '__pycache__' \
  ./ "$host:$dir/"
ssh "$host" "cd '$dir' && mkdir -p data && docker compose up -d --build && docker compose ps"
