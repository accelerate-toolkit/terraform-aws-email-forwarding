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
        outgoing = forwarder.compose((directory / 'outbound.eml').read_bytes(), self.config['forward_from'], self.config['destination'], 'fixture', 100000)
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
                patch.dict(os.environ, {'AWS_ACCESS_KEY_ID': 'fixture', 'TF_CLI_ARGS_plan': '-destroy'}), \
                patch.object(deploy.os, 'execve') as execute:
            deploy.run(self.path / 'config.local.json', ['plan', '-out=a file.tfplan'])
        binary, argv, env = execute.call_args.args
        self.assertEqual(argv[-2:], ['plan', '-out=a file.tfplan'])
        self.assertNotIn('AWS_ACCESS_KEY_ID', env)
        self.assertNotIn('-destroy', env['TF_CLI_ARGS_plan'])
        self.assertEqual(env['TF_WORKSPACE'], 'default')
        self.assertEqual(env['AWS_PROFILE'], self.config['aws_profile'])
        self.assertIn('backend.tfbackend', env['TF_CLI_ARGS_init'])

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

if __name__ == '__main__':
    unittest.main()
