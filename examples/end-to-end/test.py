# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Prepare synthetic mail; verify a downloaded forwarded message without mailbox credentials."""
import argparse
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import uuid
import zlib

import config as settings


def png():
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)
    pixels = b''.join(b'\x00' + bytes([40, 130, 210]) * 24 for _ in range(24))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 24, 24, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


def run_path(run_id):
    if not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise ValueError('Use the 32-character ID printed by prepare.')
    return settings.BUILD / 'runs' / run_id


def prepare(config):
    run_id = uuid.uuid4().hex
    directory = run_path(run_id)
    directory.mkdir(parents=True, mode=0o700)
    marker = 'forwarding-test-' + run_id
    plain = f'{marker}\nSynthetic forwarding test. No personal content.\n'
    html = f'<html><body><p>{marker}</p><p><b>HTML formatting check</b></p><img src="cid:test-image" alt="Blue test square"></body></html>'
    attachment = f'Synthetic attachment for {marker}\n'.encode()
    image = png()
    message = EmailMessage()
    message['From'] = config['test_sender']
    message['To'] = config['recipient']
    message['Subject'] = marker
    message.set_content(plain)
    message.add_alternative(html, subtype='html')
    message.get_payload()[1].add_related(image, maintype='image', subtype='png', cid='<test-image>')
    message.add_attachment(attachment, maintype='text', subtype='plain', filename='fixture.txt')
    for name, value in [('body.txt', plain.encode()), ('body.html', html.encode()), ('fixture.txt', attachment), ('inline.png', image), ('outbound.eml', message.as_bytes(policy=policy.SMTP))]:
        # Contents include local mailbox settings in outbound.eml; artifacts stay private.
        settings.private_write(directory / name, '')
        (directory / name).write_bytes(value)
    manifest = dict(run_id=run_id, marker=marker, recipient=config['recipient'],
                    destination=config['destination'], sender=config['test_sender'],
                    forward_from=config['forward_from'],
                    attachment_sha256=hashlib.sha256(attachment).hexdigest(),
                    image_sha256=hashlib.sha256(image).hexdigest(),
                    created=datetime.now(timezone.utc).isoformat(), status='pending_manual_send_and_inbox_verification')
    settings.private_write(directory / 'result.json', json.dumps(manifest, indent=2) + '\n')
    return run_id


def inspect(raw, expected):
    message = BytesParser(policy=policy.default).parsebytes(raw)
    addresses = lambda header: [a.lower() for _, a in getaddresses(message.get_all(header, []))]
    checks = {
        'subject_identifier': expected['marker'] in str(message.get('Subject', '')),
        'forwarded_sender': expected['forward_from'].lower() in addresses('From'),
        'destination': expected['destination'].lower() in addresses('To'),
        'reply_to': expected['sender'].lower() in addresses('Reply-To'),
        'plain_body': False, 'html_body': False, 'attachment': False, 'inline_image': False,
    }
    html_parts, images = [], []
    for part in message.walk():
        if part.is_multipart():
            continue
        data = part.get_payload(decode=True) or b''
        digest = hashlib.sha256(data).hexdigest()
        if part.get_filename() == 'fixture.txt' and digest == expected['attachment_sha256']:
            checks['attachment'] = True
        if part.get_content_type() in ['text/plain', 'text/html'] and part.get_content_disposition() != 'attachment':
            text = data.decode(part.get_content_charset() or 'utf-8', errors='replace')
            if part.get_content_type() == 'text/plain':
                checks['plain_body'] |= expected['marker'] in text
            else:
                html_parts.append(text)
                checks['html_body'] |= expected['marker'] in text
        if digest == expected['image_sha256'] and part.get('Content-ID'):
            images.append(str(part['Content-ID']).strip('<>'))
    checks['inline_image'] = any('cid:' + cid in html for cid in images for html in html_parts)
    return checks


def verify(run_id, received, confirm_inbox=False, confirm_rendering=False):
    directory = run_path(run_id)
    result = json.loads((directory / 'result.json').read_text())
    checks = inspect(Path(received).read_bytes(), result)
    result.update(checks=checks, inbox_confirmed=confirm_inbox, rendering_confirmed=confirm_rendering,
                  verified=datetime.now(timezone.utc).isoformat())
    result['status'] = ('failed' if not all(checks.values()) else
                        'passed' if confirm_inbox and confirm_rendering else 'pending_manual_confirmation')
    settings.private_write(directory / 'result.json', json.dumps(result, indent=2) + '\n')
    return result['status'], checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('prepare', help='Create a synthetic fixture; never sends email.')
    create.add_argument('--config', type=Path, default=settings.DEFAULT_CONFIG)
    check = commands.add_parser('verify', help='Verify the forwarded .eml downloaded from the destination inbox.')
    check.add_argument('--run-id', required=True)
    check.add_argument('--message', type=Path, required=True)
    check.add_argument('--confirm-inbox', action='store_true', help='Human confirms this file came from the expected inbox for this run.')
    check.add_argument('--confirm-rendering', action='store_true', help='Human confirms HTML, inline image and attachment render/open correctly.')
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            run_id = prepare(settings.load(args.config))
            print(f'Run: {run_id}\nFixtures: build/end-to-end/runs/{run_id}/\nStatus: pending manual send and inbox verification. No email sent.')
            return 0
        status, checks = verify(args.run_id, args.message, args.confirm_inbox, args.confirm_rendering)
        print('Status:', status)
        for name, passed in checks.items():
            print(name + ': ' + ('PASS' if passed else 'FAIL'))
        return 0 if status == 'passed' else 1
    except (ValueError, OSError, KeyError, LookupError):
        print('Test setup/verification failed; check config and private run artifacts. No email sent.', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
