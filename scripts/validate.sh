#!/usr/bin/env bash
set -euo pipefail
mode=validate
case "${1:-}" in
  --test) mode=test ;;
  --help) echo "Usage: scripts/validate.sh [--test] — validate module/example or run mocked Terraform plans in Docker."; exit 0 ;;
  "") ;;
  *) echo "Usage: scripts/validate.sh [--test]" >&2; exit 2 ;;
esac
root=$(cd "$(dirname "$0")/.." && pwd)
version=$(cat "$root/.terraform-version")
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
# Explicit allowlist: never mount private config, state, test mail or AWS credentials.
cp "$root/"*.tf "$stage/"
cp -R "$root/lambda" "$stage/"
mkdir -p "$stage/tests"
cp "$root/tests/"*.tftest.hcl "$stage/tests/"
mkdir -p "$stage/examples/end-to-end"
cp "$root/examples/end-to-end/"*.tf "$stage/examples/end-to-end/"
# Match the host owner so Linux runners can remove generated provider/module files.
container_user="$(id -u):$(id -g)"
directories=(/work /work/examples/end-to-end)
if [[ "$mode" == test ]]; then directories=(/work); fi
for directory in "${directories[@]}"; do
  docker run --rm --user "$container_user" -v "$stage:/work" -w "$directory" "hashicorp/terraform:$version" init -backend=false -input=false
  docker run --rm --user "$container_user" --network none -v "$stage:/work" -w "$directory" "hashicorp/terraform:$version" "$mode" -no-color
done
