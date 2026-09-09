"""Real git/filesystem/process checks for the shared workflow helpers."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'plugins/imps/scripts/workflow-evidence.py'
spec = importlib.util.spec_from_file_location('evidence', SCRIPT)
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.goal = self.root / 'GOAL.md'
        self.goal.write_text('## Definition of Done\n- [ ] [REQ-1] a working journey [verify:runtime]\n## Global Constraints\n- keep credentials private\n')
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        (self.repo / 'code.txt').write_text('baseline')
        self.git('add', '.')
        self.git('commit', '-qm', 'baseline')
        self.base = self.git('rev-parse', 'HEAD')
        self.state = self.root / 'state.json'
        self.state.write_text(json.dumps({'branch': 'fixture', 'tasks': [{'id': 1, 'spec': 'x' * 10000}], 'private_metadata': {'nested': 7}}))

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.PIPE).decode().strip()

    def snapshot(self):
        return evidence.snapshot(self.repo, self.base, self.goal)

    def test_checkbox_progress_does_not_change_contract(self):
        before = self.snapshot()
        self.goal.write_text(self.goal.read_text().replace('[ ]', '[x]') + '\n## Decision trail\n- progressed\n')
        self.assertEqual(before['spec_hash'], self.snapshot()['spec_hash'])

    def test_changed_requirements_and_constraints_invalidate(self):
        before = self.snapshot()
        self.goal.write_text(self.goal.read_text().replace('credentials private', 'secrets private'))
        self.assertNotEqual(before['spec_hash'], self.snapshot()['spec_hash'])

    def test_requirement_method_and_id_survive(self):
        requirement = self.snapshot()['requirements'][0]
        self.assertEqual(requirement['id'], 'REQ-1')
        self.assertEqual(requirement['method'], 'runtime')

    def test_legacy_ids_are_stable(self):
        first, _ = evidence.contract('## Definition of Done\n- [ ] old requirement')
        second, _ = evidence.contract('## Definition of Done\n- [x] old requirement')
        self.assertEqual(first, second)

    def test_duplicate_ids_and_process_only_contract_fail(self):
        for text in ['## Definition of Done\n- [ ] [REQ-1] a\n- [ ] [REQ-1] b',
                     '## Definition of Done\n- [ ] Gates green (test)']:
            with self.assertRaises(ValueError):
                evidence.contract(text)

    def test_dirty_code_cannot_be_verified(self):
        (self.repo / 'code.txt').write_text('not committed')
        self.assertFalse(self.snapshot()['clean'])

    def test_committed_change_changes_head(self):
        before = self.snapshot()
        (self.repo / 'code.txt').write_text('new commit')
        self.git('commit', '-qam', 'changed')
        self.assertNotEqual(before['head'], self.snapshot()['head'])

    def test_model_configuration_changes_policy_hash(self):
        before = self.snapshot()
        with patch.dict(os.environ, {'IMPS_CODEX_MODEL': 'different-model'}):
            self.assertNotEqual(before['policy_hash'], self.snapshot()['policy_hash'])

    def test_atomic_patch_preserves_unknown_fields_and_long_specs(self):
        owner = evidence.state_operation(self.state, 'claim')
        result = evidence.state_operation(self.state, 'patch', owner['token'], {'phase': 'done'})
        self.assertEqual(result['private_metadata'], {'nested': 7})
        self.assertEqual(len(result['tasks'][0]['spec']), 10000)
        self.assertEqual(json.loads(self.state.read_text()), result)

    def test_concurrent_claim_and_wrong_owner_are_rejected(self):
        owner = evidence.state_operation(self.state, 'claim')
        with self.assertRaises(ValueError):
            evidence.state_operation(self.state, 'claim')
        with self.assertRaises(ValueError):
            evidence.state_operation(self.state, 'patch', 'wrong', {'phase': 'bad'})
        evidence.state_operation(self.state, 'release', owner['token'])
        self.assertNotEqual(owner['token'], evidence.state_operation(self.state, 'claim')['token'])

    def test_stale_claim_needs_explicit_confirmed_recovery(self):
        owner = evidence.state_operation(self.state, 'claim')
        with self.assertRaises(ValueError):
            evidence.state_operation(self.state, 'recover', owner['token'])
        evidence.state_operation(self.state, 'recover', owner['token'], confirmed_dead=True)
        self.assertTrue(evidence.state_operation(self.state, 'claim')['token'])

    def test_budget_survives_resume(self):
        value = json.loads(self.state.read_text())
        value.update(budget_seconds=1, budget_started_at=time.time() - 5)
        self.state.write_text(json.dumps(value))
        owner = evidence.state_operation(self.state, 'claim')
        self.assertFalse(evidence.state_operation(self.state, 'budget', owner['token'])['ok'])
        evidence.state_operation(self.state, 'release', owner['token'])
        owner = evidence.state_operation(self.state, 'claim')
        self.assertFalse(evidence.state_operation(self.state, 'budget', owner['token'])['ok'])

    def test_crash_before_atomic_replace_preserves_old_state(self):
        before = self.state.read_bytes()
        with patch.object(evidence.os, 'replace', side_effect=OSError('injected crash')):
            with self.assertRaises(OSError):
                evidence.atomic_json(self.state, {'bad': True})
        self.assertEqual(before, self.state.read_bytes())

    def test_malformed_state_fails_without_overwriting(self):
        self.state.write_text('{broken')
        with self.assertRaises(ValueError):
            evidence.state_operation(self.state, 'claim')
        self.assertEqual(self.state.read_text(), '{broken')

    def test_artifact_hash_tracks_bytes(self):
        before = evidence.artifact(self.goal)
        self.goal.write_text('different')
        self.assertNotEqual(before['sha256'], evidence.artifact(self.goal)['sha256'])

    def test_timeout_kills_descendants(self):
        marker = self.root / 'orphan'
        child = 'import time,pathlib;time.sleep(0.5);pathlib.Path(' + repr(str(marker)) + ').write_text("escaped")'
        parent = 'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",' + repr(child) + ']);time.sleep(10)'
        result = subprocess.run([sys.executable, str(SCRIPT.with_name('run-bounded.py')), '0.15', sys.executable, '-c', parent], timeout=5)
        self.assertEqual(result.returncode, 124)
        time.sleep(0.6)
        self.assertFalse(marker.exists())

    def test_completed_parent_does_not_leave_review_children(self):
        marker = self.root / 'orphan'
        child = 'import time,pathlib;time.sleep(0.4);pathlib.Path(' + repr(str(marker)) + ').write_text("escaped")'
        parent = 'import subprocess,sys;subprocess.Popen([sys.executable,"-c",' + repr(child) + '])'
        result = subprocess.run([sys.executable, str(SCRIPT.with_name('run-bounded.py')), '2', sys.executable, '-c', parent], timeout=5)
        self.assertEqual(result.returncode, 0)
        time.sleep(0.5)
        self.assertFalse(marker.exists())

    def test_gate_retains_actual_exit_and_hashed_log(self):
        result = subprocess.run([sys.executable, str(SCRIPT), 'gate', '--name', 'fixture', '--cmd', 'echo evidence; exit 7', '--cwd', '.', '--timeout', '5'], cwd=self.repo, capture_output=True, text=True, check=True)
        value = json.loads(result.stdout)
        self.addCleanup(Path(value['artifact']['path']).unlink)
        self.assertFalse(value['pass'])
        self.assertEqual(value['exit_code'], 7)
        self.assertEqual(evidence.artifact(value['artifact']['path']), value['artifact'])

    def test_gate_cannot_change_directory_outside_checkout(self):
        result = subprocess.run([sys.executable, str(SCRIPT), 'gate', '--name', 'fixture', '--cmd', 'true', '--cwd', '..', '--timeout', '5'], cwd=self.repo, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_shared_helpers_are_identical(self):
        for name in ('workflow-evidence.py', 'run-bounded.py', 'run-codex-review.sh'):
            self.assertEqual((ROOT / 'plugins/imps/scripts' / name).read_bytes(), (ROOT / 'plugins/babysitter/scripts' / name).read_bytes())


if __name__ == '__main__':
    unittest.main()
