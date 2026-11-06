import json
import subprocess
import sys
import unittest

from helpers import BIN, TempRepo

SETTINGS = {"hooks": {
    "PreToolUse": [{"hooks": [{"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/dev-loop/bin/guard.py\""}]}],
    "SessionStart": [{"hooks": [{"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/dev-loop/bin/memory.py\" session-start"}]}],
}}


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()

    def tearDown(self):
        self.repo.close()

    def install(self):
        return subprocess.run([sys.executable, "-I", str(BIN / "install.py"), "--target", str(self.repo.root)],
                              capture_output=True, text=True)

    def check(self):
        return self.repo.run("install.py", "--check")

    def test_install_pins_files_scaffolds_once_and_check_detects_drift(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Wire these hooks", r.stdout)
        root = self.repo.root
        for p in (".claude/skills/objective/SKILL.md", ".claude/skills/dream/SKILL.md", ".claude/agents/dev-loop-reviewer.md",
                  ".claude/dev-loop/bin/guard.py", ".claude/memory/MEMORY.md", ".claude/dev-loop/verify.json"):
            self.assertTrue((root / p).is_file(), p)
        lock = json.loads((root / ".claude/dev-loop/dev-loop.lock.json").read_text())
        self.assertIn(".claude/dev-loop/bin/verify.py", lock["files"])
        self.assertNotIn(".claude/memory/MEMORY.md", lock["files"])

        self.assertIn("hook not wired", self.check().stdout)
        self.repo.write(".claude/settings.json", json.dumps(SETTINGS))
        self.assertEqual(self.check().returncode, 0, self.check().stdout)

        (root / ".claude/dev-loop/bin/guard.py").write_text("# weakened\n")
        r = self.check()
        self.assertEqual(r.returncode, 1)
        self.assertIn("modified .claude/dev-loop/bin/guard.py", r.stdout)

        self.repo.write(".claude/memory/MEMORY.md", "project-owned\n")
        self.install()
        self.assertEqual((root / ".claude/memory/MEMORY.md").read_text(), "project-owned\n")
        self.assertEqual(self.check().returncode, 0)


if __name__ == "__main__":
    unittest.main()
