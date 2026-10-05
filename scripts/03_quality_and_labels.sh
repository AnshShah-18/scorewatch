#!/usr/bin/env bash
# Build the data quality tables and the 24-month default target in BigQuery.
set -euo pipefail
cd "$(dirname "$0")/.."
source config.env
LOCATION=${LOCATION:-US}

run_sql () {
  echo "== $1"
  sed -e "s/__PROJECT__/$PROJECT_ID/g" -e "s/__BUCKET__/$BUCKET/g" "$1" \
    | bq --location="$LOCATION" query --use_legacy_sql=false --format=pretty \
    | grep -v '^Waiting on'
}

run_sql sql/04_data_quality.sql
run_sql sql/05_loan_outcomes.sql
