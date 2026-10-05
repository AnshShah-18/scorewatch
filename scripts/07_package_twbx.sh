#!/usr/bin/env bash
# Build dashboard/ScoreWatch.twbx (workbook + Hyper extracts) for Tableau Public.
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -c "import tableauhyperapi" 2>/dev/null || .venv/bin/pip install -q tableauhyperapi
mkdir -p data/logs
(cd data/logs && ../../.venv/bin/python ../../python/package_twbx.py)
