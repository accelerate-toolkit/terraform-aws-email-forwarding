# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Check Git publication candidates without printing private values."""
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = re.compile(r'(?<![A-Za-z0-9])[0-9]{12}(?![A-Za-z0-9])')
EMAIL = re.compile(r'[A-Za-z0-9_.+%-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})')
TOKEN = re.compile(r'(?:AKIA|ASIA)[A-Z0-9]{16}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')


def reasons(path, data, private_values=()):
    errors = []
    if any(part in ['build', '.terraform', 'apm_modules', '.agents'] for part in path.parts) or path.name.endswith(('.local.json', '.tfstate', '.tfplan', '.eml', '.tfvars', '.tfvars.json', '.tfbackend')) or '.tfstate.' in path.name:
        errors.append('private/generated path')
    text = data.decode('utf-8', errors='replace')
    if TOKEN.search(text):
        errors.append('possible credential')
    if any(value not in ['123456789012', '000000000000'] for value in ACCOUNT.findall(text)):
        errors.append('non-example account identifier')
    if any(domain.lower() not in ['example.com', 'example.net', 'example.org'] and not domain.lower().endswith(('.example.com', '.example.net', '.example.org', '.invalid', '.test')) for domain in EMAIL.findall(text)):
        errors.append('non-example mailbox')
    if any(value and value in text for value in private_values):
        errors.append('local deployment value')
    return errors


def check(root=ROOT):
    local = root / 'examples/end-to-end/config.local.json'
    values = []
    if local.exists():
        config = json.loads(local.read_text())
        fields = ['account_id', 'aws_profile', 'role_name_prefix', 'hosted_zone_id', 'domain',
                  'recipient', 'forward_from', 'destination', 'test_sender', 'alert_email']
        values = [config.get(k, '') for k in fields]
        values.append(config.get('backend', {}).get('bucket', ''))
        # Incomplete/exemplar local configs are not private identities.
        values = [v for v in values if v and v not in ['123456789012', '000000000000', 'ZEXAMPLE'] and 'example.' not in v]
    failures = []
    # Scan working copies (including untracked public candidates) AND the staged
    # blobs: a corrected working copy must not conceal an older secret in the index.
    candidates = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root).decode().split('\0')
    indexed = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0'))
    for name in sorted(set(candidates) - {''}):
        path = Path(name)
        if (root / path).is_file():
            found = reasons(path, (root / path).read_bytes(), values)
            if found:
                failures.append((name, 'working tree', found))
        if name in indexed:
            blob = subprocess.check_output(['git', 'show', ':' + name], cwd=root)
            found = reasons(path, blob, values)
            if found:
                failures.append((name, 'index', found))
    return failures


def main():
    try:
        failures = check()
    except (ValueError, OSError, subprocess.SubprocessError):
        print('Privacy check could not complete; inspect local configuration and Git state.', file=sys.stderr)
        return 2
    for path, source, why in failures:
        print(f'{path} ({source}): ' + ', '.join(why))
    if not failures:
        print('Publication candidate privacy checks passed. This is a guardrail, not a substitute for diff review.')
    return bool(failures)

if __name__ == '__main__':
    raise SystemExit(main())
