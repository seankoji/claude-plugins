"""The actual initializer allocates empty report directories without deleting research."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'plugins/ape/scripts/init-workspace.sh'


class WorkspaceTest(unittest.TestCase):
    def test_each_run_has_an_empty_report_directory_and_preserves_previous_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            env = {**os.environ, 'HOME': directory}
            def initialize():
                result = subprocess.run(['bash', str(SCRIPT)], cwd=project, env=env,
                                        capture_output=True, text=True, check=True)
                return Path(next(line.split('=', 1)[1] for line in result.stdout.splitlines()
                                 if line.startswith('reports=')))
            first = initialize()
            old = first / 'org__repo.md'
            old.write_text('previous expedition')
            second = initialize()
            self.assertNotEqual(first, second)
            self.assertEqual(list(second.iterdir()), [])
            self.assertEqual(old.read_text(), 'previous expedition')
            subprocess.run(['bash', str(SCRIPT.with_name('clean-ape-workspace.sh')),
                            str(first), '--all', '--confirm'], env=env,
                           capture_output=True, text=True, check=True)
            self.assertFalse(first.exists())
            self.assertTrue(second.is_dir())
