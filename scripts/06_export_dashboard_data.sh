#!/usr/bin/env bash
# Export the dashboard tables from BigQuery to CSV for the Tableau workbook.
set -euo pipefail
cd "$(dirname "$0")/.."
source config.env
LOCATION=${LOCATION:-US}
OUT=dashboard/data
mkdir -p "$OUT"

for t in vintage score_bands score_distribution csi scorecard state; do
  bq --quiet --location="$LOCATION" query --use_legacy_sql=false --format=csv \
     --max_rows=100000 "SELECT * FROM \`$PROJECT_ID.scorewatch.dash_$t\`" > "$OUT/$t.csv"
  printf "%-20s %6s rows\n" "$t" "$(( $(wc -l < "$OUT/$t.csv") - 1 ))"
done
echo "CSVs written to $OUT/"
