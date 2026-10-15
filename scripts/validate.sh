#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
version=$(cat "$root/.terraform-version")
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
# Explicit allowlist: never mount private config, state, test mail or AWS credentials.
cp "$root/"*.tf "$stage/"
cp -R "$root/lambda" "$stage/"
mkdir -p "$stage/examples/end-to-end"
cp "$root/examples/end-to-end/"*.tf "$stage/examples/end-to-end/"
for directory in /work /work/examples/end-to-end; do
  docker run --rm -v "$stage:/work" -w "$directory" "hashicorp/terraform:$version" init -backend=false -input=false
  docker run --rm --network none -v "$stage:/work" -w "$directory" "hashicorp/terraform:$version" validate -no-color
done
