"""Black-box tests for install-board-hook.py — stdlib only."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".claude/skills/jira-tracker/scripts/install-board-hook.py"


def run(args, cwd, env_home=None):
    env = dict(os.environ)
    if env_home:
        env["HOME"] = str(env_home)
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          cwd=cwd, capture_output=True, text=True, env=env)


class TestInstallBoardHook(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def commands(self, path):
        s = json.loads(Path(path).read_text())
        return [h["command"]
                for e in s.get("hooks", {}).get("UserPromptSubmit", [])
                for h in e.get("hooks", [])]

    def test_creates_project_settings_with_hook(self):
        r = run([], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        cmds = self.commands(self.dir / ".claude/settings.json")
        self.assertEqual(len(cmds), 1)
        self.assertIn(".jira/board.json", cmds[0])

    def test_preserves_existing_settings_and_hooks(self):
        p = self.dir / ".claude/settings.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({
            "permissions": {"allow": ["Bash(ls:*)"]},
            "hooks": {
                "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo other"}]}],
                "Stop": [{"hooks": [{"type": "command", "command": "echo done"}]}],
            },
        }))
        r = run([], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        s = json.loads(p.read_text())
        self.assertEqual(s["permissions"]["allow"], ["Bash(ls:*)"])
        self.assertEqual(len(s["hooks"]["Stop"]), 1)
        self.assertEqual(len(self.commands(p)), 2)  # theirs + ours

    def test_idempotent_rerun(self):
        run([], self.dir)
        r = run([], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("already installed", r.stdout)
        self.assertEqual(len(self.commands(self.dir / ".claude/settings.json")), 1)

    def test_global_flag_uses_home(self):
        r = run(["--global"], self.dir, env_home=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.commands(self.dir / ".claude/settings.json")), 1)

    def test_explicit_settings_path(self):
        target = self.dir / "custom/settings.json"
        r = run(["--settings", str(target)], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.commands(target)), 1)

    def test_corrupt_settings_friendly_error(self):
        p = self.dir / ".claude/settings.json"
        p.parent.mkdir(parents=True)
        p.write_text("{broken")
        r = run([], self.dir)
        self.assertEqual(r.returncode, 1)
        self.assertIn("error:", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


    def test_wrong_type_settings_friendly_error(self):
        p = self.dir / ".claude/settings.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"hooks": []}))  # valid JSON, wrong shape
        r = run([], self.dir)
        self.assertEqual(r.returncode, 1)
        self.assertIn("error:", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_symlinked_settings_updates_target(self):
        target = self.dir / "real-settings.json"
        target.write_text("{}\n")
        link = self.dir / ".claude/settings.json"
        link.parent.mkdir(parents=True)
        link.symlink_to(target)
        r = run([], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.dir / ".claude/settings.json").is_symlink())  # link preserved
        self.assertEqual(len(self.commands(target)), 1)  # real file got the hook


if __name__ == "__main__":
    unittest.main()
