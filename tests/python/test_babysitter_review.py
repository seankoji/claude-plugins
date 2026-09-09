"""Real shell adapter with local git and scripted reviewer output; no API calls."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / 'plugins/babysitter/scripts/ocr-gate.sh'

class BabysitterReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.repo / 'code').write_text('base')
        self.git('add', '.')
        self.git('commit', '-qm', 'base')
        subprocess.run(['git', 'clone', '--bare', '-q', str(self.repo), str(self.root / 'remote')], check=True)
        self.git('remote', 'add', 'origin', str(self.root / 'remote'))
        self.git('checkout', '-qb', 'change')
        (self.repo / 'code').write_text('change')
        self.git('commit', '-qam', 'change')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = {**os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
                    'IMPS_CODEX_PLUGIN_ROOT': str(self.root / 'missing'),
                    'IMPS_CLAUDE_PLUGINS_MANIFEST': str(self.root / 'missing.json')}
        # Keep a machine's installed custom wrapper from affecting these fixtures.
        wrapper = self.bin / 'ocr-pre-pr.sh'
        wrapper.write_text('#!/bin/sh\nocr review > "$OCR_RESULT_PATH"\n')
        wrapper.chmod(0o755)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.PIPE).decode().strip()

    def run_gate(self, payload, code=0, mutate=False):
        stub = self.bin / 'ocr'
        stub.write_text('#!/bin/sh\n[ "$1" = review ] || exit 1\n' +
                        ('printf changed-again > code\n' if mutate else '') +
                        "printf '%s\\n' '" + json.dumps(payload) + "'\nexit " + str(code) + '\n')
        stub.chmod(0o755)
        return subprocess.run(['bash', str(GATE), '--base', 'main'], cwd=self.repo, env=self.env,
                              capture_output=True, text=True, timeout=15)

    def test_valid_clean_binds_head_and_base(self):
        result = self.run_gate({'comments': []})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('status=clean', result.stdout)
        self.assertIn('head=' + self.git('rev-parse', 'HEAD'), result.stdout)

    def test_missing_comments_is_not_clean(self):
        result = self.run_gate({})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('status=clean', result.stdout)

    def test_success_with_findings_stays_adverse(self):
        result = self.run_gate({'comments': [{'body': 'defect'}]})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('status=findings', result.stdout)

    def test_mutation_blocks_review(self):
        result = self.run_gate({'comments': []}, mutate=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn('status=clean', result.stdout)

    def test_cached_base_is_explicit_when_refresh_fails(self):
        self.git('fetch', '-q', 'origin', 'main')
        self.git('remote', 'set-url', 'origin', str(self.root / 'unavailable'))
        result = self.run_gate({'comments': []})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('base_fresh=false', result.stdout)
        self.assertIn('cached base', result.stderr)
