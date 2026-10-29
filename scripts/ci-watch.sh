#!/bin/sh
set -eu

sha=$(git rev-parse HEAD)
run_id=''
attempt=0
while [ -z "$run_id" ] && [ "$attempt" -lt 30 ]; do
    run_id=$(gh run list --commit "$sha" --limit 1 --json databaseId --jq '.[0].databaseId // empty')
    [ -n "$run_id" ] || sleep 4
    attempt=$((attempt + 1))
done
[ -n "$run_id" ] || { echo "no CI run for $sha" >&2; exit 1; }
gh run watch "$run_id" --exit-status
