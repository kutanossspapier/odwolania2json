#!/usr/bin/env bash
# Przetwarza PDF-y z ./input do ./output (JSON). Przy pierwszym uruchomieniu sam tworzy .venv.
# Użycie: ./run.sh            (z LLM, jeśli skonfigurowany w .env; inaczej same regexy)
#         ./run.sh --no-llm   (tylko regexy)
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONUTF8=1
[ -f .env ] && set -a && . ./.env && set +a || true
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi
mkdir -p input output
exec .venv/bin/python -m odw.cli --input input --output output "$@"
