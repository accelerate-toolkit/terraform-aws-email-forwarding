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
               'forward_from', 'destination_a', 'destination_b', 'test_sender', 'alert_email']
    if any(not isinstance(config.get(k), str) or not config[k].strip() or '\n' in config[k] for k in strings):
        raise ValueError('Required configuration strings must be non-empty single-line values.')
    if not re.fullmatch(r'\d{12}', config['account_id']):
        raise ValueError('account_id must contain 12 digits.')
    if not re.fullmatch(r'AWSReservedSSO_[A-Za-z0-9_]+_', config['role_name_prefix']):
        raise ValueError('Use a specific AWSReservedSSO permission-set role prefix, ending in _.')
    if not isinstance(config.get('enable_mx'), bool):
        raise ValueError('enable_mx must be an explicit boolean.')
    if any(not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+', config[k]) for k in ['forward_from', 'destination_a', 'destination_b', 'test_sender', 'alert_email']):
        raise ValueError('Mail settings must be plain email addresses without display names.')
    optional = {'runtime_role_path', 'runtime_permissions_boundary_arn', 'simulate_destination_b_failure'}
    if set(config) - optional != set(strings) | {'enable_mx', 'backend'}:
        raise ValueError('Unexpected configuration keys; credentials do not belong in this file.')
    if config['destination_a'].lower() == config['destination_b'].lower():
        raise ValueError('Configure two distinct external destination inboxes.')
    if any(address.lower().endswith('@' + config['domain'].lower())
           for address in [config['destination_a'], config['destination_b']]):
        raise ValueError('Destination inboxes must be outside the receiving domain.')
    if not isinstance(config.setdefault('simulate_destination_b_failure', False), bool):
        raise ValueError('simulate_destination_b_failure must be boolean.')
    role_path = config.setdefault('runtime_role_path', '/')
    boundary = config.setdefault('runtime_permissions_boundary_arn', None)
    if not isinstance(role_path, str) or not re.fullmatch(r'/([!-~]+/)?', role_path):
        raise ValueError('Runtime role path must begin and end with /.')
    if boundary is not None and (not isinstance(boundary, str) or not re.fullmatch(r'arn:aws:iam::[0-9]{12}:policy/[^\s]+', boundary)):
        raise ValueError('Runtime permissions boundary must be an IAM policy ARN or null.')
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
