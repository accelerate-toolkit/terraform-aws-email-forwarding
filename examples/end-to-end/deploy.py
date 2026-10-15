# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Select private test configuration and forward native Terraform arguments."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import config as settings


def aws(arguments, config, env):
    result = subprocess.run(['aws', *arguments, '--profile', config['aws_profile'],
                             '--region', config['region'], '--output', 'json', '--no-cli-pager'],
                            env=env, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError('AWS read failed; check the configured SSO session and permissions.')
    return json.loads(result.stdout or '{}')


def verify_caller(caller, config):
    prefix = f"arn:aws:sts::{config['account_id']}:assumed-role/{config['role_name_prefix']}"
    arn = caller.get('Arn', '')
    if caller.get('Account') != config['account_id'] or not arn.startswith(prefix) or '/' not in arn[len(prefix):]:
        raise RuntimeError('Caller does not match the configured account and SSO role.')


def prepare(config):
    values = {k: config[k] for k in ['account_id', 'region', 'resource_prefix', 'hosted_zone_id',
                                    'domain', 'receipt_rule_set_name', 'recipient', 'forward_from',
                                    'destination', 'alert_email', 'enable_mx']}
    variables = settings.BUILD / 'inputs.tfvars.json'
    backend = settings.BUILD / 'backend.tfbackend'
    settings.private_write(variables, json.dumps(values, indent=2) + '\n')
    options = dict(config['backend'], encrypt=True, use_lockfile=True, allowed_account_ids=[config['account_id']])
    settings.private_write(backend, '\n'.join(f'{k} = {json.dumps(v)}' for k, v in options.items()) + '\n')
    return variables, backend


def run(config_path, args):
    if os.environ.get('CI') or os.environ.get('GITHUB_ACTIONS'):
        raise RuntimeError('Live test deployments are human-operated locally; no CI AWS access.')
    if not args or any(arg.startswith('-chdir') for arg in args):
        raise ValueError('Pass a Terraform command without overriding the root directory.')
    config = settings.load(config_path, live=True)
    env = settings.clean_environment()
    caller = aws(['sts', 'get-caller-identity'], config, env)
    verify_caller(caller, config)
    if args[0] in {'plan', 'apply'}:
        active = aws(['ses', 'describe-active-receipt-rule-set'], config, env)
        if active.get('Metadata', {}).get('Name') != config['receipt_rule_set_name']:
            raise RuntimeError('The configured receipt rule set must already be active in this account/region.')
    binary = shutil.which(config['terraform_binary'])
    if not binary:
        raise RuntimeError('Install the pinned Terraform version or set terraform_binary in the private config.')
    version = subprocess.run([binary, 'version', '-json'], env=env, capture_output=True, text=True, check=True)
    if json.loads(version.stdout)['terraform_version'] != (settings.ROOT / '.terraform-version').read_text().strip():
        raise RuntimeError('Terraform binary does not match .terraform-version.')
    os.umask(0o077)
    variables, backend = prepare(config)
    env.update(AWS_PROFILE=config['aws_profile'], AWS_REGION=config['region'], AWS_DEFAULT_REGION=config['region'],
               AWS_EC2_METADATA_DISABLED='true', AWS_PAGER='', TF_WORKSPACE='default',
               TF_DATA_DIR=str(settings.BUILD / 'terraform-data'))
    Path(env['TF_DATA_DIR']).mkdir(parents=True, exist_ok=True, mode=0o700)
    env['TF_CLI_ARGS_init'] = '-backend-config=' + shlex.quote(str(backend))
    for command in ['plan', 'apply', 'destroy', 'refresh', 'import', 'console']:
        env['TF_CLI_ARGS_' + command] = '-var-file=' + shlex.quote(str(variables))
        if command != 'console':
            env['TF_CLI_ARGS_' + command] += ' -lock-timeout=30s'
    os.execve(binary, [binary, '-chdir=' + str(settings.HERE), *args], env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=settings.DEFAULT_CONFIG)
    parser.add_argument('terraform_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        run(args.config, args.terraform_args)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
        # Avoid echoing subprocess command/output or malformed private values.
        print('Setup failed. Check private config, named SSO login, active rule set and pinned Terraform. No automatic fallback.', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
