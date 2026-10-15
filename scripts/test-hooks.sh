#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
cd "$fixture"
git init -q
git config user.name 'Local hook fixture'
git config user.email 'fixture@example.invalid'
printf 'old\n' >apm.yml
git add apm.yml
git commit -qm 'test: initial fixture'
git update-ref ORIG_HEAD HEAD
[ -z "$(bash "$root/scripts/apm-reminder.sh" post-merge)" ]
printf 'new\n' >apm.yml
git commit -qam 'test: change dependency fixture'
printf 'unrelated\n' >README.md
git add README.md
git commit -qm 'test: unrelated final commit'
for event in post-merge 'post-rewrite rebase'; do
  read -r -a args <<<"$event"
  result=$(bash "$root/scripts/apm-reminder.sh" "${args[@]}" </dev/null)
  [[ "$result" == *'Run make apm-install'* ]]
done
[ -z "$(bash "$root/scripts/apm-reminder.sh" post-rewrite amend </dev/null)" ]
git update-ref -d ORIG_HEAD
[ -z "$(bash "$root/scripts/apm-reminder.sh" post-merge)" ]
echo 'APM reminders: changed dependency across multiple commits, unchanged tree, amend and missing history passed.'
