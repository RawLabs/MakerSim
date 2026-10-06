#!/usr/bin/env bash
set -euo pipefail
makersim_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$makersim_root"
makersim_qa_directory="$(mktemp -d /tmp/makersim-qa.XXXXXX)"
export MAKERSIM_QA_DIRECTORY="$makersim_qa_directory"
trap 'rm -rf -- "$makersim_qa_directory"' EXIT
.venv/bin/python -m pytest -q
node tests/test_viewer.mjs --prepare
.venv/bin/python -m scripts.solver_fixture < "$makersim_qa_directory/request.json" > "$makersim_qa_directory/result.json"
node tests/test_viewer.mjs
(cd frontend && npm run build)
