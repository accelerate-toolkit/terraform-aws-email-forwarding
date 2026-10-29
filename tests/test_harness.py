# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'examples/end-to-end'))
import config
import deploy
import suite
import hashlib
from email import policy
from email.parser import BytesParser

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

mail_test = load('mail_test', ROOT / 'examples/end-to-end/test.py')
forwarder = load('forwarder', ROOT / 'lambda/forwarder.py')
privacy = load('privacy', ROOT / 'scripts/privacy_check.py')

class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.config = json.loads((ROOT / 'examples/end-to-end/config.example.json').read_text())

    def test_example_cannot_be_used_for_live_deployment(self):
        with self.assertRaisesRegex(ValueError, 'Replace example'):
            config.load(ROOT / 'examples/end-to-end/config.example.json', live=True)

    def test_private_config_rejects_embedded_credentials(self):
        self.config['backend']['access_key'] = 'fixture'
        source = self.path / 'config.local.json'; source.write_text(json.dumps(self.config))
        with self.assertRaisesRegex(ValueError, 'only bucket'):
            config.load(source)

    def test_identity_guards_reject_root_static_user_other_account_and_role(self):
        good = {'Account': '123456789012', 'Arn': 'arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_ExampleAdmin_abcd/session'}
        deploy.verify_caller(good, self.config)
        for arn in ['arn:aws:iam::123456789012:root', 'arn:aws:iam::123456789012:user/test', good['Arn'].replace('ExampleAdmin','OtherRole'), good['Arn'].replace('123456789012','000000000000')]:
            with self.assertRaises(RuntimeError):
                deploy.verify_caller(dict(good, Arn=arn), self.config)

    def test_terraform_inputs_are_private_and_use_expected_backend(self):
        with patch.object(config, 'BUILD', self.path):
            variables, backend = deploy.prepare(self.config)
        self.assertEqual(variables.stat().st_mode & 0o777, 0o600)
        self.assertEqual(backend.stat().st_mode & 0o777, 0o600)
        self.assertIn('allowed_account_ids = ["123456789012"]', backend.read_text())
        self.assertNotIn('test_sender', json.loads(variables.read_text()))

    def fixture(self):
        with patch.object(config, 'BUILD', self.path):
            run_id = mail_test.prepare(self.config)
        directory = self.path / 'runs' / run_id
        outgoing = forwarder.compose((directory / 'outbound.eml').read_bytes(), self.config['forward_from'], self.config['destination_a'], 'fixture', 100000)
        received = directory / 'received.eml'; received.write_bytes(outgoing)
        return run_id, directory, received

    def test_wrapper_passes_native_args_and_clears_ambient_credentials(self):
        caller = {'Account': '123456789012', 'Arn': 'arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_ExampleAdmin_abcd/session'}
        version = json.dumps({'terraform_version': (ROOT / '.terraform-version').read_text().strip()})
        with patch.object(config, 'BUILD', self.path), \
                patch.object(config, 'load', return_value=self.config), \
                patch.object(deploy, 'aws', side_effect=[caller, {'Metadata': {'Name': self.config['receipt_rule_set_name']}}]), \
                patch.object(deploy.shutil, 'which', return_value='/terraform'), \
                patch.object(deploy.subprocess, 'run', return_value=Mock(stdout=version)), \
                patch.dict(os.environ, {'AWS_ACCESS_KEY_ID': 'fixture', 'TF_CLI_ARGS_plan': '-destroy'}, clear=True), \
                patch.object(deploy.os, 'execve') as execute:
            deploy.run(self.path / 'config.local.json', ['plan', '-out=a file.tfplan'])
        binary, argv, env = execute.call_args.args
        self.assertEqual(argv[-2:], ['plan', '-out=a file.tfplan'])
        self.assertNotIn('AWS_ACCESS_KEY_ID', env)
        self.assertNotIn('-destroy', env['TF_CLI_ARGS_plan'])
        self.assertEqual(env['TF_WORKSPACE'], 'default')
        self.assertEqual(env['AWS_PROFILE'], self.config['aws_profile'])
        self.assertIn('backend.tfbackend', env['TF_CLI_ARGS_init'])

    def test_ci_deployment_is_blocked_before_configuration_or_aws_access(self):
        for flag in ['CI', 'GITHUB_ACTIONS']:
            with self.subTest(flag=flag), patch.dict(os.environ, {flag: 'true'}, clear=True), \
                    patch.object(config, 'load') as load_config, \
                    patch.object(deploy, 'aws') as aws, \
                    patch.object(deploy.os, 'execve') as execute:
                with self.assertRaisesRegex(RuntimeError, 'no CI AWS access'):
                    deploy.run(self.path / 'config.local.json', ['plan'])
                load_config.assert_not_called()
                aws.assert_not_called()
                execute.assert_not_called()

    def test_valid_mail_requires_manual_confirmation(self):
        run_id, directory, received = self.fixture()
        with patch.object(config, 'BUILD', self.path):
            status, checks = mail_test.verify(run_id, received)
            self.assertTrue(all(checks.values()))
            self.assertEqual(status, 'pending_manual_confirmation')
            self.assertEqual(mail_test.verify(run_id, received, True, True)[0], 'passed')

    def test_wrong_run_and_original_message_cannot_pass(self):
        run_id, directory, received = self.fixture()
        with patch.object(config, 'BUILD', self.path):
            self.assertEqual(mail_test.verify(run_id, directory / 'outbound.eml', True, True)[0], 'failed')
            other = mail_test.prepare(self.config)
            self.assertEqual(mail_test.verify(other, received, True, True)[0], 'failed')
            with self.assertRaises(ValueError):
                mail_test.run_path('../../private')

    def test_corrupt_attachment_and_missing_inline_image_are_detected(self):
        run_id, directory, received = self.fixture()
        expected = json.loads((directory / 'result.json').read_text())
        expected['attachment_sha256'] = 'wrong'; expected['image_sha256'] = 'wrong'
        checks = mail_test.inspect(received.read_bytes(), expected)
        self.assertFalse(checks['attachment']); self.assertFalse(checks['inline_image'])

    def test_privacy_check_flags_tracked_private_file_even_if_working_copy_is_fixed(self):
        subprocess.run(['git','init','-q'],cwd=self.path,check=True)
        p = self.path / 'main.tf'; p.write_text('account = "' + '1' * 12 + '"')
        subprocess.run(['git','add','main.tf'],cwd=self.path,check=True)
        p.write_text('account = "123456789012"')
        failures = privacy.check(self.path)
        self.assertTrue(any(source == 'index' for _, source, _ in failures))
        self.assertTrue(privacy.reasons(Path('config.local.json'), b'{}'))
        self.assertEqual(privacy.reasons(Path('example.json'), b'"tester@example.org"'), [])
        self.assertEqual(privacy.reasons(Path('apm.lock.yaml'), ('hash: a' + '1' * 12 + 'b').encode()), [])

    def test_setup_diagnostics_classify_aws_failures_without_private_output(self):
        for detail, expected in [
            ('Error loading SSO Token for private@example.net', 'SSO session is missing or expired'),
            ('The config profile private@example.net could not be found', 'profile was not found'),
            ('AccessDenied private@example.net', 'Caller identity check failed'),
        ]:
            with patch.object(deploy.subprocess, 'run', return_value=Mock(returncode=1, stderr=detail)):
                with self.assertRaises(deploy.SetupError) as caught:
                    deploy.aws(['sts', 'get-caller-identity'], self.config, {})
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn('private@example.net', str(caught.exception))
        with patch.object(deploy.subprocess, 'run', side_effect=FileNotFoundError('private path')):
            with self.assertRaisesRegex(deploy.SetupError, 'AWS CLI was not found'):
                deploy.aws(['sts', 'get-caller-identity'], self.config, {})


    def test_two_inboxes_and_optional_fault_are_validated(self):
        source = self.path / 'config.local.json'
        for changes in [dict(destination_b=self.config['destination_a']),
                        dict(destination_b='inbox@' + self.config['domain']),
                        dict(simulate_destination_b_failure='true'),
                        dict(runtime_permissions_boundary_arn='not-an-arn')]:
            source.write_text(json.dumps(dict(self.config, **changes)))
            with self.assertRaises(ValueError):
                config.load(source)
        source.write_text(json.dumps(self.config))
        loaded = config.load(source)
        self.assertFalse(loaded['simulate_destination_b_failure'])
        with patch.object(config, 'BUILD', self.path):
            variables, _ = deploy.prepare(dict(loaded, simulate_destination_b_failure=True))
        self.assertTrue(json.loads(variables.read_text())['simulate_destination_b_failure'])
        self.assertNotIn('recipient', json.loads(variables.read_text()))
        self.assertEqual(json.loads(variables.read_text())['destination_b'], loaded['destination_b'])

    def suite_evidence(self, run_id, name):
        case = suite.load(run_id)['cases'][name]
        directory = mail_test.run_path(case['fixture_id'])
        message = BytesParser(policy=policy.default).parsebytes((directory / 'outbound.eml').read_bytes())
        del message['Bcc']  # SMTP client removes Bcc; SES event retains the envelope recipient.
        raw = directory / 'downloaded.eml'; raw.write_bytes(message.as_bytes())
        message_id = 'ses-' + name
        record = {'ses': {'mail': {'messageId': message_id, 'destination': case['recipients']},
                          'receipt': {'recipients': case['recipients']}}}
        rules = {
            'acceptance': {'patterns': ['original-*', 'replacement-*'], 'forward_to': self.config['destination_a']},
            'overlap': {'patterns': ['original-*'], 'forward_to': self.config['destination_b']},
            'support': {'patterns': ['support'], 'forward_to': self.config['destination_a']},
        }
        metadata = forwarder.routing_metadata(record, {'RECIPIENT_DOMAIN': self.config['domain'], 'FORWARDING_RULES': json.dumps(rules)})
        metadata_file = directory / 'downloaded.json'; metadata_file.write_text(json.dumps(metadata))
        return case, directory, message_id, metadata_file, raw

    def test_routing_suite_requires_all_storage_inboxes_and_observations(self):
        with patch.object(config, 'BUILD', self.path):
            run_id = suite.prepare(self.config)
            self.assertEqual(len(suite.load(run_id)['cases']), 6)
            self.assertEqual(set(suite.summary(run_id).values()), {'pending'})
            for name in suite.load(run_id)['cases']:
                case, directory, message_id, metadata, raw = self.suite_evidence(run_id, name)
                self.assertFalse(all(suite.verify_storage(run_id, name, message_id, metadata, raw).values()))
                self.assertTrue(all(suite.verify_storage(run_id, name, message_id, metadata, raw, True).values()))
                for label in case['expected_inboxes']:
                    received = directory / ('inbox-' + label + '.eml')
                    received.write_bytes(forwarder.compose(raw.read_bytes(), self.config['forward_from'], self.config['destination_' + label], 'fixture', 100000))
                    self.assertFalse(all(suite.verify_inbox(run_id, name, label, received).values()))
                    self.assertTrue(all(suite.verify_inbox(run_id, name, label, received, True, True).values()))
                self.assertNotEqual(suite.summary(run_id)[name], 'passed')
                suite.observe(run_id, name, int('a' in case['expected_inboxes']), int('b' in case['expected_inboxes']), 10)
                self.assertEqual(suite.summary(run_id)[name], 'passed')
            self.assertEqual(set(suite.summary(run_id).values()), {'passed'})
            other = suite.prepare(self.config)
            self.assertNotEqual(suite.load(other)['cases']['original']['recipients'], suite.load(run_id)['cases']['original']['recipients'])
            manifest = mail_test.run_path(run_id) / 'suite.json'
            self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)

    def test_suite_rejects_mismatched_storage_and_wrong_inbox(self):
        with patch.object(config, 'BUILD', self.path):
            run_id = suite.prepare(self.config)
            case, directory, message_id, metadata, raw = self.suite_evidence(run_id, 'multiple')
            wrong = json.loads(metadata.read_text()); wrong['receipt_recipients'] = case['recipients'][:1]
            metadata.write_text(json.dumps(wrong))
            checks = suite.verify_storage(run_id, 'multiple', message_id, metadata, raw, True)
            self.assertFalse(checks['receipt_recipients'])
            self.assertEqual(suite.summary(run_id)['multiple'], 'failed')
            with self.assertRaises(ValueError):
                suite.verify_inbox(run_id, 'replacement', 'b', raw, True, True)
            with self.assertRaises(ValueError):
                suite.observe(run_id, 'original', -1, 0, 10)
            with self.assertRaises(ValueError):
                suite.observe(run_id, 'original', 1, 1, 0)
            self.assertFalse(suite.observe(run_id, 'original', 2, 1, 10))
            self.assertFalse(suite.observe(run_id, 'unmatched', 1, 0, 10))

    def test_storage_rejects_wrong_run_mime_and_exposed_bcc(self):
        with patch.object(config, 'BUILD', self.path):
            run_id = suite.prepare(self.config)
            case, directory, message_id, metadata, raw = self.suite_evidence(run_id, 'multiple')
            checks = suite.verify_storage(run_id, 'multiple', message_id, metadata, directory / 'outbound.eml', True)
            self.assertFalse(checks['bcc_not_visible'])
            other = suite.prepare(self.config)
            other_case = suite.load(other)['cases']['multiple']
            other_raw = mail_test.run_path(other_case['fixture_id']) / 'outbound.eml'
            self.assertFalse(suite.verify_storage(run_id, 'multiple', message_id, metadata, other_raw, True)['subject_identifier'])

    def test_replacing_storage_evidence_invalidates_prior_conclusions(self):
        with patch.object(config, 'BUILD', self.path):
            run_id = suite.prepare(self.config, recovery=True)
            case, directory, message_id, metadata, raw = self.suite_evidence(run_id, 'original')
            suite.verify_storage(run_id, 'original', message_id, metadata, raw, True)
            suite.observe(run_id, 'original', 1, 1, 10)
            state = suite.load(run_id)
            state['checkpoints'] = {'failed': {'passed': True}}
            suite.save(run_id, state)
            # Different JSON bytes must trigger renewed recovery evidence even
            # when the routing values still pass their schema/content checks.
            metadata.write_text(json.dumps(json.loads(metadata.read_text()), indent=2))
            self.assertTrue(all(suite.verify_storage(run_id, 'original', message_id, metadata, raw, True).values()))
            state = suite.load(run_id)
            self.assertFalse(state['cases']['original']['observation'])
            self.assertNotIn('checkpoints', state)
            key = json.loads((directory / 'ledger-key-a.json').read_text())
            self.assertTrue(key['id']['S'].startswith(message_id + '#'))
            self.assertEqual((directory / 'ledger-key-a.json').stat().st_mode & 0o777, 0o600)

    def test_recovery_checkpoints_require_original_keys_and_unchanged_success(self):
        with patch.object(config, 'BUILD', self.path):
            run_id = suite.prepare(self.config, recovery=True)
            case, directory, message_id, metadata, raw = self.suite_evidence(run_id, 'original')
            suite.verify_storage(run_id, 'original', message_id, metadata, raw, True)
            paths = {label: directory / ('ledger-' + label + '.json') for label in ['a', 'b']}
            def ledger(label, status, delivery):
                key = message_id + '#' + hashlib.sha256(self.config['destination_' + label].lower().encode()).hexdigest()
                paths[label].write_text(json.dumps({'Item': {'id': {'S': key}, 'status': {'S': status}, 'delivery_id': {'S': delivery}}}))
            ledger('a', 'sent', 'delivery-a'); ledger('b', 'processing', '')
            self.assertFalse(all(suite.verify_ledger(run_id, 'failed', paths['a'], paths['b']).values()))
            self.assertTrue(all(suite.verify_ledger(run_id, 'failed', paths['a'], paths['b'], True).values()))
            self.assertTrue(all(suite.verify_ledger(run_id, 'replayed', paths['a'], paths['b'], True).values()))
            ledger('a', 'sent', 'changed-delivery')
            self.assertFalse(suite.verify_ledger(run_id, 'replayed', paths['a'], paths['b'], True)['a_delivery_unchanged'])
            ledger('a', 'sent', 'delivery-a')
            suite.verify_ledger(run_id, 'replayed', paths['a'], paths['b'], True)
            ledger('b', 'sent', 'delivery-b')
            self.assertTrue(all(suite.verify_ledger(run_id, 'recovered', paths['a'], paths['b'], True).values()))
            self.assertEqual(suite.summary(run_id)['ledger_recovered'], 'passed')
            self.assertEqual(suite.summary(run_id)['original'], 'pending')

if __name__ == '__main__':
    unittest.main()
