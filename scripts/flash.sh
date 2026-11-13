#!/bin/zsh
set -euo pipefail
project="${0:A:h:h}"
if ! command -v uv >/dev/null 2>&1; then
  print 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/' >&2
  exit 1
fi
export UV_PROJECT_ENVIRONMENT="$project/.app-venv"
# --project locates dependencies without changing the caller's working directory.
exec uv run --project "$project" --frozen --python "${QFT_PYTHON:-3.11.13}" --managed-python qft-flash "$@"
