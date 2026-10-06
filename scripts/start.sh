#!/usr/bin/env bash
set -euo pipefail
makersim_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$makersim_root"
if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c 'import fastapi, scipy, numpy, uvicorn, python_multipart' >/dev/null 2>&1; then
  uv sync
fi
if [[ ! -d frontend/node_modules/vite ]]; then
  (cd frontend && npm install --no-audit --no-fund)
fi
(cd frontend && npm run build)
exec .venv/bin/python -m backend.serve "$@"
