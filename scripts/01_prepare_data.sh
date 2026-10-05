#!/usr/bin/env bash
# Unzip Freddie Mac SFLLD sample files, verify column counts, and gzip each
# text file for upload. Layout: Freddie Mac SFLLD Release 47 (July 2026).
# Stops if a file's layout doesn't match the schema.
set -euo pipefail
cd "$(dirname "$0")/.."

RAW=${RAW:-data/raw}
STAGED=data/staged
EXPECTED_ORIG=31
EXPECTED_PERF=35

mkdir -p "$STAGED"
shopt -s nullglob
zips=("$RAW"/sample_*.zip)
if [[ ${#zips[@]} -eq 0 ]]; then
  echo "No sample_*.zip files found in $RAW" >&2
  exit 1
fi

printf "%-6s %-5s %8s %14s\n" "year" "file" "columns" "rows"
bad=0
for z in "${zips[@]}"; do
  yr=$(basename "$z" .zip | sed 's/sample_//')
  tmp=$(mktemp -d)
  unzip -q -o "$z" -d "$tmp"
  for kind in orig perf; do
    f=$(find "$tmp" -name "sample_${kind}_${yr}.txt" | head -1)
    if [[ -z "$f" ]]; then
      echo "Missing sample_${kind}_${yr}.txt inside $z. Contents:" >&2
      find "$tmp" -type f >&2
      exit 1
    fi
    cols=$(head -1 "$f" | awk -F'|' '{print NF}')
    rows=$(wc -l < "$f" | tr -d ' ')
    expected=$EXPECTED_ORIG; [[ $kind == perf ]] && expected=$EXPECTED_PERF
    flag=""; if [[ "$cols" -ne "$expected" ]]; then flag="  <-- expected $expected"; bad=1; fi
    printf "%-6s %-5s %8s %14s%s\n" "$yr" "$kind" "$cols" "$rows" "$flag"
    gzip -c "$f" > "$STAGED/sample_${kind}_${yr}.txt.gz"
  done
  rm -rf "$tmp"
done

if [[ $bad -ne 0 ]]; then
  echo
  echo "Column count mismatch. Don't load yet; send this output so the schema can be fixed." >&2
  exit 1
fi
echo
echo "All files OK. Gzipped copies are in $STAGED/"
