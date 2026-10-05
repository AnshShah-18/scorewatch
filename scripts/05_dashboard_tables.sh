#!/usr/bin/env bash
# Build the small dashboard tables that feed Google Sheets and Tableau Public.
set -euo pipefail
cd "$(dirname "$0")/.."
source config.env
LOCATION=${LOCATION:-US}

echo "== sql/07_dashboard_tables.sql"
sed -e "s/__PROJECT__/$PROJECT_ID/g" sql/07_dashboard_tables.sql \
  | bq --location="$LOCATION" query --use_legacy_sql=false --format=pretty \
  | grep -v '^Waiting on'
