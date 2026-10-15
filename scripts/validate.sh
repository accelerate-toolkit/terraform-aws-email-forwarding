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
# Match the host owner so Linux runners can remove generated provider/module files.
container_user="$(id -u):$(id -g)"
for directory in /work /work/examples/end-to-end; do
  docker run --rm --user "$container_user" -v "$stage:/work" -w "$directory" "hashicorp/terraform:$version" init -backend=false -input=false
  docker run --rm --user "$container_user" --network none -v "$stage:/work" -w "$directory" "hashicorp/terraform:$version" validate -no-color
done
