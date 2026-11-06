import json
import unittest

from helpers import TempRepo, git

PROFILE = {
    "base": "main",
    "max_corrections": 3,
    "ignore": ["\\.md$"],
    "test_paths": ["(^|/)tests?/"],
    "checks": [
        {"id": "integrity", "builtin": "integrity", "always": True},
        {"id": "unit", "when": ["^src/"], "run": "test -f src/ok"},
        {"id": "docs-only", "when": ["^docs/"], "run": "true"},
        {"id": "needs-tool", "when": ["^src/"], "run": "true", "requires": [{"cmd": "definitely-not-installed-xyz"}]},
        {"id": "app", "when": ["."], "unless": ["^server/"], "also_when": ["^server/shared\\.txt$"], "run": "true"},
        {"id": "device", "manual": True, "when": ["."], "run": "true"},
    ],
}


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.repo.write(".claude/dev-loop/verify.json", json.dumps(PROFILE))
        self.repo.write("tests/test_a.py", "def test_a():\n    assert 1 == 1\n")
        self.repo.commit("profile")
        git(self.repo.root, "checkout", "-q", "-b", "feature")

    def tearDown(self):
        self.repo.close()

    def verify(self, *args):
        return self.repo.run("verify.py", "--objective", "obj", *args)

    def test_selects_checks_by_changed_paths_and_reports_unavailable(self):
        self.repo.write("src/ok", "")
        r = self.verify()
        self.assertIn("[ok  ] unit", r.stdout)
        self.assertIn("[n/a ] needs-tool", r.stdout)
        self.assertNotIn("docs-only", r.stdout)
        self.assertIn("overall: INCOMPLETE", r.stdout)
        self.assertEqual(r.returncode, 2)

    def test_markdown_changes_alone_select_nothing_but_always_checks(self):
        self.repo.write("src/notes.md", "x")
        r = self.verify("--list")
        self.assertIn("would run integrity", r.stdout)
        self.assertNotIn("would run unit", r.stdout)

    def test_blocks_after_three_failed_corrections_and_resets_on_pass(self):
        self.repo.write("src/code", "")
        codes = [self.verify("--only", "unit").returncode for _ in range(4)]
        self.assertEqual(codes, [1, 1, 1, 3])
        r = self.verify("--only", "unit")
        self.assertIn("STOP", r.stdout)
        self.repo.write("src/ok", "")
        r = self.verify("--only", "unit")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.verify("--only", "unit").returncode, 0)

    def test_ledger_is_per_objective(self):
        self.repo.write("src/code", "")
        for _ in range(4):
            self.verify("--only", "unit")
        other = self.repo.run("verify.py", "--objective", "other", "--only", "unit")
        self.assertEqual(other.returncode, 1)

    def test_integrity_fails_on_new_skip_marker(self):
        self.repo.write("tests/test_a.py", "import unittest\n@unittest.skip('later')\ndef test_a():\n    assert 1 == 1\n")
        r = self.verify("--only", "integrity")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("unittest", r.stdout)

    def test_integrity_fails_on_deleted_test_and_accepts_an_approved_waiver(self):
        (self.repo.root / "tests/test_a.py").unlink()
        self.assertEqual(self.verify("--only", "integrity").returncode, 1)
        r = self.verify("--only", "integrity", "--waive", "integrity:deleted-test")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("WAIVED", r.stdout)

    def test_integrity_accepts_in_code_waiver_and_warns_on_safeguard_change(self):
        self.repo.write("src/x.ts", "// @ts-ignore dev-loop: allow EXC-2026-009\nconst a = 1\n")
        self.repo.write(".github/workflows/ci.yml", "on: push\n")
        r = self.verify("--only", "integrity")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("waived in code (EXC-2026-009)", r.stdout)
        self.assertIn("safeguard or CI changed", r.stdout)

    def test_unless_also_when_and_manual(self):
        self.repo.write("server/a.ts", "x")
        self.assertNotIn("would run app", self.verify("--list").stdout)
        self.repo.write("server/shared.txt", "x")
        out = self.verify("--list").stdout
        self.assertIn("would run app", out)
        self.assertNotIn("would run device", out)
        self.assertNotIn("would run device", self.verify("--list", "--all").stdout)
        self.assertIn("would run device", self.verify("--list", "--only", "device").stdout)

    def test_suppression_is_a_warning_not_a_failure(self):
        self.repo.write("src/x.kt", "@Suppress(\"UNUSED\")\nval a = 1\n")
        r = self.verify("--only", "integrity")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("suppression added", r.stdout)

    def test_integrity_warns_when_assertions_are_removed(self):
        self.repo.write("tests/test_a.py", "def test_a():\n    pass\n")
        r = self.verify("--only", "integrity")
        self.assertIn("assertion lines removed", r.stdout)

    def test_writes_evidence(self):
        self.repo.write("src/ok", "")
        r = self.verify("--only", "unit")
        path = r.stdout.strip().split("evidence ")[-1].split()[0]
        with open(path, encoding="utf-8") as fh:
            summary = json.load(fh)
        self.assertEqual(summary["overall"], "PASS")
        self.assertEqual(summary["results"][0]["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
