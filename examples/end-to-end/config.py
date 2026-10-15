"""Private deployment configuration shared by the Terraform and mail-test tools."""
import json
import os
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_CONFIG = HERE / 'config.local.json'
BUILD = ROOT / 'build/end-to-end'


def load(path=DEFAULT_CONFIG, *, live=False):
    try:
        config = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise ValueError('Create config.local.json from config.example.json; keep real settings private.') from None
    if not isinstance(config, dict):
        raise ValueError('Configuration must be a JSON object.')
    strings = ['aws_profile', 'account_id', 'role_name_prefix', 'region', 'terraform_binary',
               'resource_prefix', 'hosted_zone_id', 'domain', 'receipt_rule_set_name',
               'recipient', 'forward_from', 'destination', 'test_sender', 'alert_email']
    if any(not isinstance(config.get(k), str) or not config[k].strip() or '\n' in config[k] for k in strings):
        raise ValueError('Required configuration strings must be non-empty single-line values.')
    if not re.fullmatch(r'\d{12}', config['account_id']):
        raise ValueError('account_id must contain 12 digits.')
    if not re.fullmatch(r'AWSReservedSSO_[A-Za-z0-9_]+_', config['role_name_prefix']):
        raise ValueError('Use a specific AWSReservedSSO permission-set role prefix, ending in _.')
    if not isinstance(config.get('enable_mx'), bool):
        raise ValueError('enable_mx must be an explicit boolean.')
    if any(not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+', config[k]) for k in ['recipient', 'forward_from', 'destination', 'test_sender', 'alert_email']):
        raise ValueError('Mail settings must be plain email addresses without display names.')
    if set(config) != set(strings) | {'enable_mx', 'backend'}:
        raise ValueError('Unexpected configuration keys; credentials do not belong in this file.')
    backend = config.get('backend', {})
    if not isinstance(backend, dict) or set(backend) != {'bucket', 'key', 'region'}:
        raise ValueError('Backend accepts only bucket, key and region; use SSO for credentials.')
    if any(not isinstance(backend.get(k), str) or not backend[k].strip() for k in ['bucket', 'key', 'region']):
        raise ValueError('Configure the existing private state backend: bucket, key and region.')
    if live and (config['account_id'] in ['123456789012', '000000000000'] or config['hosted_zone_id'] == 'ZEXAMPLE'):
        raise ValueError('Replace example settings before contacting AWS.')
    return config


def private_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(value)


def clean_environment():
    return {k: v for k, v in os.environ.items() if not k.startswith(('AWS_', 'TF_'))}
