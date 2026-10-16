# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Release planning and partial-publication tests without GitHub writes."""
import json
from pathlib import Path
import subprocess
import tempfile
import os
import unittest
from unittest.mock import patch
import release
import consumer


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, old)
        subprocess.run(['git', 'init', '-q'], check=True)
        release.git('config', 'user.name', 'Test')
        release.git('config', 'user.email', 'test@example.org')

    def commit(self, subject, body=''):
        release.git('commit', '--allow-empty', '-qm', subject, '-m', body)

    def test_initial_minor_and_body_notes(self):
        self.commit('feat: forward messages', 'Preserve MIME content.')
        result = release.plan()
        self.assertEqual(result['version'], '0.1.0')
        self.assertIn('Preserve MIME content.', result['notes'])

    def test_docs_only_no_release(self):
        self.commit('docs: clarify usage')
        self.assertIsNone(release.plan())

    def test_breaking_zero_major(self):
        self.commit('feat: initial')
        release.git('tag', 'v0.1.0')
        self.commit('fix!: change inputs')
        self.assertEqual(release.plan()['version'], '1.0.0')

    def test_patch_then_minor_precedence(self):
        self.commit('feat: initial')
        release.git('tag', 'v0.1.0')
        self.commit('fix: correct forwarding')
        self.assertEqual(release.plan()['version'], '0.1.1')
        self.commit('feat: add input')
        self.assertEqual(release.plan()['version'], '0.2.0')

    def test_breaking_footer(self):
        self.commit('fix: change inputs', 'BREAKING CHANGE: rename input')
        self.assertEqual(release.plan()['version'], '1.0.0')

    def test_resume_unpublished_tag_before_new_work(self):
        self.commit('feat: initial')
        release.git('tag', 'v0.1.0')
        expected = release.git('rev-parse', 'HEAD')
        self.commit('feat: later feature')
        with patch.object(release, 'gh', return_value='[[]]'):
            result = release.plan('example/module')
        self.assertEqual(result['version'], '0.1.0')
        self.assertEqual(result['commit'], expected)
        self.assertNotIn('later feature', result['notes'])


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.notes = Path(self.tmp.name) / 'notes.md'
        self.notes.write_text('Release notes')
        self.tag = False
        self.draft = None
        self.sha = 'a' * 40
        self.calls = []
        self.fail_edit = False

    def gh(self, *args):
        self.calls.append(args)
        if 'git/matching-refs' in ' '.join(args):
            return json.dumps([{'ref': 'refs/tags/v0.1.0'}] if self.tag else [])
        if 'repos/example/module/commits/v0.1.0' in args:
            return json.dumps({'sha': self.sha})
        if '--method' in args:
            self.tag = True
        elif '--paginate' in args:
            return json.dumps([[{'tag_name': 'v0.1.0', 'draft': self.draft}]] if self.draft is not None else [[]])
        elif args[:2] == ('release', 'create'):
            self.draft = True
        elif args[:2] == ('release', 'edit'):
            if self.fail_edit:
                raise RuntimeError('simulated publication failure')
            self.draft = False
        return ''

    def publish(self):
        with patch.object(release, 'git'), patch.object(release, 'gh', side_effect=self.gh):
            release.publish('example/module', '0.1.0', 'a' * 40, self.notes)

    def test_repeat_is_noop(self):
        self.publish()
        self.publish()
        self.assertEqual(sum(c[:2] == ('release', 'create') for c in self.calls), 1)
        self.assertEqual(sum(c[:2] == ('release', 'edit') for c in self.calls), 1)

    def test_draft_recovered_after_failure(self):
        self.fail_edit = True
        with self.assertRaises(RuntimeError):
            self.publish()
        self.fail_edit = False
        self.publish()
        self.assertFalse(self.draft)
        self.assertEqual(sum(c[:2] == ('release', 'create') for c in self.calls), 1)

    def test_tag_without_release_is_reused(self):
        self.tag = True
        self.publish()
        self.assertFalse(self.draft)
        self.assertFalse(any('--method' in c for c in self.calls))

    def test_conflicting_tag_refused(self):
        self.tag = True
        self.sha = 'b' * 40
        with self.assertRaisesRegex(ValueError, 'refusing to move'):
            self.publish()
        self.assertFalse(any(c[0] == 'release' for c in self.calls))

    def test_api_error_propagates(self):
        with patch.object(release, 'git'), patch.object(release, 'gh', side_effect=RuntimeError('denied')):
            with self.assertRaises(RuntimeError):
                release.publish('example/module', '0.1.0', 'a' * 40, self.notes)


class WorkflowOutputTests(unittest.TestCase):
    def test_published_version_reaches_consumer_step(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'outputs'
            with patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}):
                release.output_version('0.1.0')
            self.assertEqual(output.read_text(), 'version=0.1.0\n')

    def test_no_new_release_retries_consumer_verification(self):
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'GITHUB_REF': 'refs/heads/main'}), \
             patch('sys.argv', ['release.py', 'publish', '--repo', 'example/module']), \
             patch.object(release, 'git', return_value=''), \
             patch.object(release, 'plan', return_value=None), \
             patch.object(release, 'tags', return_value=['v0.1.0']), \
             patch.object(release, 'output_version') as output, \
             patch.object(release, 'publish') as publish:
            release.main()
        output.assert_called_once_with('0.1.0')
        publish.assert_not_called()


class ConsumerTests(unittest.TestCase):
    def test_exact_tag_and_public_contract(self):
        config = consumer.configuration('example/module', '0.1.0')
        self.assertEqual(config['module']['forwarding']['source'],
                         'git::https://github.com/example/module.git?ref=v0.1.0')
        self.assertEqual(len(config['output']), 5)

    def test_rejects_branch_and_injected_source(self):
        for repo, version in [('example/module', 'main'), ('example/module?ref=other', '0.1.0')]:
            with self.assertRaises(ValueError):
                consumer.configuration(repo, version)

    def test_disposable_download_and_offline_validation(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            work = Path(args[args.index('-v') + 1].removesuffix(':/work'))
            if 'init' in args:
                module = work / '.terraform/modules/forwarding'
                (module / 'lambda').mkdir(parents=True)
                (module / 'lambda/forwarder.py').write_text('value = 1')
                (work / '.terraform/modules/modules.json').write_text(json.dumps(
                    {'Modules': [{'Key': 'forwarding', 'Dir': '.terraform/modules/forwarding'}]}))
        with patch.object(consumer.subprocess, 'run', side_effect=run):
            consumer.verify('example/module', '0.1.0')
        self.assertIn('-backend=false', calls[0])
        self.assertIn('--network', calls[1])
        self.assertIn('none', calls[1])
        self.assertFalse(any('-e' in c or 'apply' in c or 'plan' in c for c in calls))
        work = Path(calls[0][calls[0].index('-v') + 1].removesuffix(':/work'))
        self.assertFalse(work.exists())


if __name__ == '__main__':
    unittest.main()
