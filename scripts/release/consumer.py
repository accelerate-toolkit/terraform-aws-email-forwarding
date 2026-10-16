# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Validate a published Terraform Git module in a disposable, credential-free consumer."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def configuration(repo, version):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('repo must be OWNER/REPO')
    if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version):
        raise ValueError('version must be X.Y.Z')
    return {'module': {'forwarding': {
        'source': f'git::https://github.com/{repo}.git?ref=v{version}',
        'name': 'consumer-fixture', 'receipt_rule_set_name': 'existing-set',
        'domain': 'example.org', 'recipient': 'contact@example.org',
        'forward_from': 'forwarder@example.org', 'forward_to': 'inbox@example.net',
        'alarm_topic_arn': 'arn:aws:sns:eu-west-2:123456789012:fixture-alerts',
    }}, 'output': {name: {'value': '${module.forwarding.' + name + '}'} for name in
                    ['dns_records', 'raw_bucket', 'failure_queue_url', 'delivery_table', 'function_name']}}


def verify(repo, version):
    config = configuration(repo, version)
    root = Path(__file__).resolve().parents[2]
    image = 'hashicorp/terraform:' + (root / '.terraform-version').read_text().strip()
    with tempfile.TemporaryDirectory(prefix='module-consumer-') as directory:
        work = Path(directory)
        (work / 'main.tf.json').write_text(json.dumps(config))
        base = ['docker', 'run', '--rm', '--user', f'{os.getuid()}:{os.getgid()}', '-v', f'{work}:/work', '-w', '/work']
        # Explicit mounts only: no host credentials, environment, state or local module.
        subprocess.run(base + [image, 'init', '-backend=false', '-input=false'], check=True)
        subprocess.run(base + ['--network', 'none', image, 'validate', '-no-color'], check=True)
        modules = json.loads((work / '.terraform/modules/modules.json').read_text())['Modules']
        entry = next(item for item in modules if item['Key'] == 'forwarding')
        source = work / entry['Dir'] / 'lambda/forwarder.py'
        compile(source.read_text(), 'forwarder.py', 'exec')
    print(f'Published v{version}: consumer inputs/outputs and bundled Lambda validated; no AWS calls.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    verify(args.repo, args.version)


if __name__ == '__main__':
    main()
