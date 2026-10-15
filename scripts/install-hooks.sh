#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
hooks=$(git rev-parse --git-path hooks)
if [ -n "$(git config --get core.hooksPath || true)" ]; then
  echo 'Existing core.hooksPath retained. See docs/development.md to integrate APM reminders.'
  exit 0
fi
mkdir -p "$hooks"
for hook in post-merge post-rewrite; do
  target="$hooks/$hook"
  if [ -e "$target" ]; then
    echo "Retained existing $hook; see docs/development.md for reminder integration."
    continue
  fi
  cat >"$target" <<EOF
#!/usr/bin/env bash
# Repository advisory APM reminder; no installs or network access.
root=\$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
bash "\$root/scripts/apm-reminder.sh" $hook "\$@" || true
exit 0
EOF
  chmod +x "$target"
  echo "Installed $hook APM reminder."
done
