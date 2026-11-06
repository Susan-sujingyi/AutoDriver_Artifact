#!/usr/bin/env bash
set -euo pipefail
artifact_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$artifact_dir"
export PYTHONDONTWRITEBYTECODE=1
mkdir -p run
exec .venv/bin/python -m unittest discover -s tests/regression "$@"
