#!/usr/bin/env bash
set -euo pipefail
if [ "$#" != 1 ] || [ "$1" = --help ]; then
  echo 'Usage: bash scripts/renovate-extract.sh VERSION (normally make renovate-extract)'
  exit 0
fi
root=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$root/build"
stage=$(mktemp -d "$root/build/renovate-extract.XXXXXX")
trap 'rm -rf "$stage"' EXIT
# Explicit public-input allowlist: never mount config.local.json or test artifacts.
cp "$root/renovate.json" "$root/Makefile" "$root/apm.yml" "$root/apm.lock.yaml" \
  "$root/.terraform-version" "$root/.node-version" "$stage/"
cp "$root/"*.tf "$stage/"
mkdir -p "$stage/.github/workflows" "$stage/examples/end-to-end" "$stage/scripts" "$stage/tests"
cp "$root/.github/workflows/"*.yml "$stage/.github/workflows/"
cp "$root/examples/end-to-end/"*.tf "$root/examples/end-to-end/"*.py "$stage/examples/end-to-end/"
cp "$root/scripts/"*.py "$stage/scripts/"
cp "$root/tests/"*.py "$stage/tests/"
docker run --rm -v "$stage:/work:ro" -w /work -e LOG_LEVEL=debug \
  "ghcr.io/renovatebot/renovate:$1" --platform=local --dry-run=extract \
  >"$root/build/renovate-extract.log" 2>&1
echo 'Dependency extraction saved to build/renovate-extract.log; no token or AWS access supplied.'
