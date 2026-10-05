#!/usr/bin/env bash
# Build the modeling table, train the scorecard, and run validation.
set -euo pipefail
cd "$(dirname "$0")/.."
source config.env
LOCATION=${LOCATION:-US}

if [[ ! -x .venv/bin/python ]]; then
  echo "== Creating Python environment (.venv)"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi

echo "== sql/06_model_base.sql"
sed -e "s/__PROJECT__/$PROJECT_ID/g" sql/06_model_base.sql \
  | bq --location="$LOCATION" query --use_legacy_sql=false --format=pretty \
  | grep -v '^Waiting on'

# Fresh pull of model_base each run (cached as data/cache/model_base.parquet)
rm -f data/cache/model_base.parquet

echo "== Training scorecard"
.venv/bin/python python/train_scorecard.py

echo "== Validating"
.venv/bin/python python/validate.py

echo "== Calibrating"
.venv/bin/python python/calibrate.py
