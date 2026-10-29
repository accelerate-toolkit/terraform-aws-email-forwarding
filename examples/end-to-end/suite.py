# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Prepare a routing matrix and verify manually downloaded S3/inbox evidence; never sends mail."""
import argparse
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
import hashlib
import json
from pathlib import Path
import sys
import uuid

import config as settings
import test as fixtures


def save(run_id, suite):
    settings.private_write(fixtures.run_path(run_id) / 'suite.json', json.dumps(suite, indent=2) + '\n')


def load(run_id, case=None):
    suite = json.loads((fixtures.run_path(run_id) / 'suite.json').read_text())
    if case is not None and case not in suite['cases']:
        raise ValueError('Unknown test case.')
    return suite


def prepare(config, recovery=False):
    run_id = uuid.uuid4().hex
    domain = config['domain']
    original, replacement = (f'{prefix}-{run_id}@{domain}' for prefix in ['original', 'replacement'])
    suite = dict(schema_version=1, run_id=run_id, mode='recovery' if recovery else 'routing', created=datetime.now(timezone.utc).isoformat(),
                 destinations={label: config['destination_' + label].lower() for label in ['a', 'b']}, cases={})
    matrix = [
        ('original', original, None, {'a': ['acceptance'], 'b': ['overlap']}),
        ('replacement', replacement, None, {'a': ['acceptance']}),
        ('multiple', original, replacement, {'a': ['acceptance'], 'b': ['overlap']}),
        ('literal', f'support@{domain}', None, {'a': ['support']}),
        ('plus', f'support+{run_id}@{domain}', None, {}),
        ('unmatched', f'unmatched-{run_id}@{domain}', None, {}),
    ]
    if recovery:
        matrix = matrix[:1]
    for name, recipient, bcc, rules in matrix:
        fixture_id = fixtures.prepare(dict(config, recipient=recipient, bcc=bcc))
        recipients = [recipient] + ([bcc] if bcc else [])
        routes = {suite['destinations'][label]: {
            'rules': names,
            'recipients': sorted(recipients if label == 'a' else [recipient]),
        } for label, names in rules.items()}
        suite['cases'][name] = dict(fixture_id=fixture_id, recipients=recipients, bcc=bcc,
                                   routes=routes, expected_inboxes=list(rules), storage={}, inboxes={}, observation={})
    save(run_id, suite)
    return run_id


def preserve(directory, name, source):
    data = Path(source).read_bytes()
    settings.private_write(directory / name, '')
    (directory / name).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def verify_inbox(run_id, case, inbox, message, confirm_inbox=False, confirm_rendering=False):
    suite = load(run_id, case)
    item = suite['cases'][case]
    if inbox not in item['expected_inboxes']:
        raise ValueError('This inbox is not expected for the selected case; record an observation instead.')
    directory = fixtures.run_path(item['fixture_id'])
    expected = json.loads((directory / 'result.json').read_text())
    expected['destination'] = suite['destinations'][inbox]
    checks = fixtures.inspect(Path(message).read_bytes(), expected)
    checks.update(inbox_confirmed=confirm_inbox, rendering_confirmed=confirm_rendering)
    digest = preserve(directory, f'received-{inbox}.eml', message)
    item['inboxes'][inbox] = dict(checks=checks, sha256=digest, passed=all(checks.values()))
    save(run_id, suite)
    return checks


def verify_storage(run_id, case, message_id, metadata_file, mime_file, confirm_s3=False):
    suite = load(run_id, case)
    item = suite['cases'][case]
    directory = fixtures.run_path(item['fixture_id'])
    expected = json.loads((directory / 'result.json').read_text())
    metadata = json.loads(Path(metadata_file).read_text())
    if not isinstance(metadata, dict):
        raise ValueError('Expected metadata object.')
    raw = Path(mime_file).read_bytes()
    message = BytesParser(policy=policy.default).parsebytes(raw)
    content = fixtures.inspect(raw, expected)
    addresses = lambda header: [address.lower() for _, address in getaddresses(message.get_all(header, []))]
    # Explicit expectations from the matrix, independent of the forwarder's matcher.
    checks = {name: content[name] for name in ['subject_identifier', 'plain_body', 'html_body', 'attachment', 'inline_image']}
    checks.update(
        original_sender=expected['sender'].lower() in addresses('From'),
        schema=metadata.get('schema_version') == 1,
        message_id=metadata.get('message_id') == message_id,
        mime_key=metadata.get('mime_key') == 'incoming/' + message_id,
        envelope_recipients=sorted(metadata.get('envelope_recipients', [])) == sorted(item['recipients']),
        receipt_recipients=sorted(metadata.get('receipt_recipients', [])) == sorted(item['recipients']),
        routes=metadata.get('routes') == item['routes'],
        outcome=metadata.get('outcome') == ('matched' if item['routes'] else 'filtered'),
        bcc_not_visible=not item['bcc'] or item['bcc'].lower() not in addresses('To') + addresses('Cc') + addresses('Bcc'),
        s3_source_confirmed=confirm_s3,
    )
    hashes = dict(metadata=preserve(directory, 'metadata.json', metadata_file),
                  mime=preserve(directory, 'raw.eml', mime_file))
    previous = item['storage']
    if previous and (previous.get('message_id') != message_id or previous.get('hashes') != hashes):
        item['inboxes'] = {}
        item['observation'] = {}
        suite.pop('checkpoints', None)
    item['storage'] = dict(checks=checks, hashes=hashes, message_id=message_id, passed=all(checks.values()))
    if all(checks.values()):
        for label in item['expected_inboxes']:
            key = message_id + '#' + hashlib.sha256(suite['destinations'][label].encode()).hexdigest()
            settings.private_write(directory / f'ledger-key-{label}.json', json.dumps({'id': {'S': key}}) + '\n')
    save(run_id, suite)
    return checks


def observe(run_id, case, count_a, count_b, minutes):
    if min(count_a, count_b) < 0 or minutes <= 0:
        raise ValueError('Counts must be nonnegative and the observation window positive.')
    suite = load(run_id, case)
    item = suite['cases'][case]
    counts = {'a': count_a, 'b': count_b}
    item['observation'] = dict(counts=counts, minutes=minutes,
        recorded=datetime.now(timezone.utc).isoformat(),
        passed=all(counts[label] == int(label in item['expected_inboxes']) for label in counts))
    save(run_id, suite)
    return item['observation']['passed']


def verify_ledger(run_id, phase, ledger_a, ledger_b, confirm_aws=False):
    suite = load(run_id, 'original')
    if suite['mode'] != 'recovery':
        raise ValueError('Ledger checkpoints require a recovery run.')
    case = suite['cases']['original']
    message_id = case['storage']['message_id']
    entries = {label: json.loads(Path(path).read_text()).get('Item', {})
               for label, path in [('a', ledger_a), ('b', ledger_b)]}
    value = lambda label, field: entries[label].get(field, {}).get('S')
    checkpoints = suite.setdefault('checkpoints', {})
    initial = checkpoints.get('failed', {})
    checks = dict(aws_source_confirmed=confirm_aws, storage_verified=case['storage'].get('passed') is True)
    for label in ['a', 'b']:
        expected_key = message_id + '#' + hashlib.sha256(suite['destinations'][label].encode()).hexdigest()
        checks[label + '_key'] = value(label, 'id') == expected_key
        expected_status = 'sent' if label == 'a' or phase == 'recovered' else 'processing'
        checks[label + '_status'] = value(label, 'status') == expected_status
        if expected_status == 'sent':
            checks[label + '_delivery_id'] = bool(value(label, 'delivery_id'))
    if phase != 'failed':
        checks['initial_checkpoint'] = initial.get('passed') is True
        checks['a_delivery_unchanged'] = value('a', 'delivery_id') == initial.get('a_delivery_id')
        checks['stored_objects_unchanged'] = case['storage']['hashes'] == initial.get('storage_hashes')
    if phase == 'recovered':
        checks['replay_checkpoint'] = checkpoints.get('replayed', {}).get('passed') is True
    directory = fixtures.run_path(case['fixture_id'])
    for label, path in [('a', ledger_a), ('b', ledger_b)]:
        preserve(directory, f'ledger-{phase}-{label}.json', path)
    # A revised earlier checkpoint invalidates downstream conclusions.
    for later in {'failed': ['replayed', 'recovered'], 'replayed': ['recovered'], 'recovered': []}[phase]:
        checkpoints.pop(later, None)
    checkpoints[phase] = dict(checks=checks, passed=all(checks.values()),
                             a_delivery_id=value('a', 'delivery_id'), storage_hashes=case['storage']['hashes'])
    save(run_id, suite)
    return checks


def summary(run_id):
    suite = load(run_id)
    statuses = {}
    for name, case in suite['cases'].items():
        results = [case['storage'], case['observation']] + [case['inboxes'].get(label, {}) for label in case['expected_inboxes']]
        statuses[name] = ('failed' if any(result.get('passed') is False for result in results) else
                          'passed' if all(result.get('passed') is True for result in results) else 'pending')
    if suite['mode'] == 'recovery':
        for phase in ['failed', 'replayed', 'recovered']:
            result = suite.get('checkpoints', {}).get(phase, {})
            statuses['ledger_' + phase] = ('passed' if result.get('passed') is True else
                                          'failed' if result.get('passed') is False else 'pending')
    return statuses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('prepare', help='Generate six cases with shared dynamic-address run ID; no AWS or email calls.')
    create.add_argument('--config', type=Path, default=settings.DEFAULT_CONFIG)
    create.add_argument('--recovery', action='store_true', help='Prepare only the original case for manual fault/replay testing; no fault is enabled automatically.')
    for name in ['verify-inbox', 'verify-storage', 'verify-ledger', 'observe', 'summary']:
        command = commands.add_parser(name)
        command.add_argument('--run-id', required=True)
        if name not in ['summary', 'verify-ledger']:
            command.add_argument('--case', required=True)
        if name == 'verify-inbox':
            command.add_argument('--inbox', choices=['a', 'b'], required=True)
            command.add_argument('--message', type=Path, required=True)
            command.add_argument('--confirm-inbox', action='store_true')
            command.add_argument('--confirm-rendering', action='store_true')
        elif name == 'verify-storage':
            command.add_argument('--message-id', required=True, help='SES message ID from the S3 object key, not the MIME Message-ID header.')
            command.add_argument('--metadata', type=Path, required=True)
            command.add_argument('--mime', type=Path, required=True)
            command.add_argument('--confirm-s3', action='store_true', help='Human confirms these files were downloaded from the expected deployment and object keys.')
        elif name == 'verify-ledger':
            command.add_argument('--phase', choices=['failed', 'replayed', 'recovered'], required=True)
            command.add_argument('--ledger-a', type=Path, required=True, help='AWS CLI DynamoDB get-item JSON, preserving AttributeValue types.')
            command.add_argument('--ledger-b', type=Path, required=True)
            command.add_argument('--confirm-aws', action='store_true', help='Human confirms fresh, consistent reads from the expected deployment and phase.')
        elif name == 'observe':
            command.add_argument('--count-a', type=int, required=True, help='Copies with this case marker observed in inbox A, including spam/trash.')
            command.add_argument('--count-b', type=int, required=True)
            command.add_argument('--minutes', type=int, required=True, help='Actual elapsed observation window after sending; this command does not wait.')
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            run_id = prepare(settings.load(args.config), args.recovery)
            print(f'Run: {run_id}\nPrivate manifest: build/end-to-end/runs/{run_id}/suite.json')
            for name, case in load(run_id)['cases'].items():
                print(f'{name}: build/end-to-end/runs/{case["fixture_id"]}/outbound.eml')
            print('Pending manual send, S3 retrieval and inbox observations. No email sent.')
            return 0
        if args.command == 'summary':
            statuses = summary(args.run_id)
            for name, status in statuses.items():
                print(f'{name}: {status}')
            print('Human-attested evidence for a bounded observation window; routing runs exclude recovery tests.')
            return 0 if all(status == 'passed' for status in statuses.values()) else 1
        if args.command == 'observe':
            passed = observe(args.run_id, args.case, args.count_a, args.count_b, args.minutes)
            print('Observation: ' + ('PASS' if passed else 'FAIL'))
            return 0 if passed else 1
        if args.command == 'verify-ledger':
            checks = verify_ledger(args.run_id, args.phase, args.ledger_a, args.ledger_b, args.confirm_aws)
        elif args.command == 'verify-inbox':
            checks = verify_inbox(args.run_id, args.case, args.inbox, args.message, args.confirm_inbox, args.confirm_rendering)
        else:
            checks = verify_storage(args.run_id, args.case, args.message_id, args.metadata, args.mime, args.confirm_s3)
        for name, passed in checks.items():
            print(name + ': ' + ('PASS' if passed else 'FAIL'))
        return 0 if all(checks.values()) else 1
    except (ValueError, OSError, KeyError, TypeError, LookupError):
        print('Suite verification failed; inspect private configuration and artifacts. No email sent.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
