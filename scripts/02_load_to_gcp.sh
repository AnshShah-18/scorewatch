#!/usr/bin/env bash
# Upload staged files to Cloud Storage, then build the raw (external) and
# typed (native) BigQuery tables and run sanity checks.
set -euo pipefail
cd "$(dirname "$0")/.."
source config.env

: "${PROJECT_ID:?Set PROJECT_ID in config.env}"
: "${BUCKET:?Set BUCKET in config.env}"
LOCATION=${LOCATION:-US}

gcloud config set project "$PROJECT_ID" >/dev/null

echo "== Bucket"
if ! gsutil ls -b "gs://$BUCKET" >/dev/null 2>&1; then
  gsutil mb -l "$LOCATION" "gs://$BUCKET"
fi

echo "== Upload"
gsutil -m cp -n data/staged/sample_orig_*.txt.gz "gs://$BUCKET/raw/orig/"
gsutil -m cp -n data/staged/sample_perf_*.txt.gz "gs://$BUCKET/raw/perf/"

echo "== Datasets"
for ds in scorewatch_raw scorewatch; do
  bq --location="$LOCATION" show "$PROJECT_ID:$ds" >/dev/null 2>&1 \
    || bq --location="$LOCATION" mk -d "$PROJECT_ID:$ds"
done

run_sql () {
  echo "== $1"
  sed -e "s/__PROJECT__/$PROJECT_ID/g" -e "s/__BUCKET__/$BUCKET/g" "$1" \
    | bq --location="$LOCATION" query --use_legacy_sql=false --format=pretty
}

run_sql sql/01_external_tables.sql
run_sql sql/02_typed_tables.sql
run_sql sql/03_sanity_checks.sql
