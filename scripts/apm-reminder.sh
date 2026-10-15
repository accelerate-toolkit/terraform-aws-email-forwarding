#!/usr/bin/env bash
# Advisory only: never make Git fail, install dependencies or contact the network.
case "${1:-}" in
  post-merge) ;;
  post-rewrite)
    [ "${2:-}" = rebase ] || exit 0
    cat >/dev/null
    ;;
  *) exit 0 ;;
esac
before=$(git rev-parse --verify ORIG_HEAD 2>/dev/null) || exit 0
changed=$(git diff --name-only "$before" HEAD -- apm.yml apm.lock.yaml 2>/dev/null) || exit 0
if [ -n "$changed" ]; then
  printf '\nAPM dependencies changed.\nRun make apm-install to refresh your skills and instructions.\n'
fi
exit 0
