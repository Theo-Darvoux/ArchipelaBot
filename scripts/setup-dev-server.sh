#!/usr/bin/env bash
# Installs a local Archipelago server and generates a ROM-free 3-player seed, used by the integration tests.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .dev && cd .dev
[ -d Archipelago ] || git clone --depth 1 https://github.com/ArchipelagoMW/Archipelago.git
cd Archipelago
[ -d .venv ] || uv venv --python 3.12 .venv
VIRTUAL_ENV=.venv uv pip install -q -r requirements.txt pip "setuptools<81"
cd ..
mkdir -p players
printf 'name: Alice\ngame: Celeste 64\nCeleste 64: {}\n' > players/p1.yaml
printf 'name: Bob\ngame: A Short Hike\nA Short Hike: {}\n' > players/p2.yaml
printf 'name: Carol\ngame: ChecksFinder\nChecksFinder: {}\n' > players/p3.yaml
rm -rf seeds
yes "" | Archipelago/.venv/bin/python Archipelago/Generate.py --player_files_path players --outputpath seeds
