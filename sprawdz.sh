#!/usr/bin/env bash
# Sprawdza dokument po dokumencie, co jest dobrze, a co źle. Opcje: --bad --fields --only odwolanie --llm
cd "$(dirname "$0")"
export PYTHONUTF8=1
exec .venv/bin/python -m odw.check "$@"
