import json
import unittest

from helpers import TempRepo

GOOD = """# Lessons

## L-001: Apply invariant headers last
- Type: mistake
- Status: verified
- Scope: server/src/http.ts
- Rule: Spread caller headers first and the invariant headers last.
- Evidence: journal/2026-11-06-x.md
- Seen: 1 (2026-11-06)
- Updated: 2026-11-06

## L-002: Maybe
- Type: solution
- Status: assumption
- Scope: build
- Rule: Probably faster with two workers.
- Evidence: none yet; a timed run would prove it
- Updated: 2026-11-06
"""


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.repo.write(".claude/memory/MEMORY.md", "# index\n")
        self.repo.write(".claude/memory/LESSONS.md", GOOD)

    def tearDown(self):
        self.repo.close()

    def lint(self):
        return self.repo.run("memory.py", "lint")

    def test_good_memory_passes(self):
        r = self.lint()
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_verified_without_evidence_unknown_status_and_temporary_without_expiry_fail(self):
        bad = GOOD.replace("- Evidence: journal/2026-11-06-x.md", "- Evidence: none")
        bad += "\n## L-003: x\n- Type: hunch\n- Status: maybe\n- Rule: r\n- Evidence: e\n- Updated: 2026-11-06\n"
        bad += "\n## L-004: y\n- Type: environment\n- Status: temporary\n- Rule: r\n- Evidence: e\n- Updated: 2026-11-06\n"
        bad += "\n## L-005: z\n- Type: avoid\n- Status: superseded\n- Rule: r\n- Evidence: e\n- Updated: 2026-11-06\n"
        self.repo.write(".claude/memory/LESSONS.md", bad)
        out = self.lint().stdout
        self.assertIn("L-001: a verified lesson needs evidence", out)
        self.assertIn("type 'hunch'", out)
        self.assertIn("status 'maybe'", out)
        self.assertIn("needs '- Expires: YYYY-MM-DD'", out)
        self.assertIn("say what supersedes it", out)

    def test_duplicate_ids_fail(self):
        self.repo.write(".claude/memory/LESSONS.md", GOOD + GOOD.split("# Lessons")[1])
        self.assertIn("L-001 used twice", self.lint().stdout)

    def test_secrets_and_personal_data_fail(self):
        self.repo.write(".claude/memory/journal/2026-11-06-x.md",
                        "---\ndate: 2026-11-06\nkind: failure\noutcome: fixed\n---\ntoken ghp_" + "a" * 36 +
                        "\nmail jane.doe@gmail.com\nok test@example.com\n")
        out = self.lint().stdout
        self.assertIn("GitHub token", out)
        self.assertIn("email address jan...", out)
        self.assertNotIn("tes...", out)

    def test_size_budget(self):
        self.repo.write(".claude/memory/MEMORY.md", "line\n" * 81)
        self.assertIn("over 80 lines", self.lint().stdout)

    def test_objective_needs_approval_and_known_status(self):
        self.repo.write(".claude/objectives/2026-11-06-a.md", "---\nid: OBJ-a\nstatus: wip\n---\n")
        out = self.lint().stdout
        self.assertIn("status 'wip'", out)
        self.assertIn("'approved:'", out)

    def test_session_start_lists_open_objectives_and_counts(self):
        self.repo.write(".claude/objectives/2026-11-06-a.md",
                        "---\nid: OBJ-a\nstatus: in-progress\napproved: owner\n---\n- [x] T1 done\n- [ ] T2 write tests\n")
        out = self.repo.run("memory.py", "session-start").stdout
        self.assertIn("2 lessons (1 assumption, 1 verified)", out)
        self.assertIn("OBJ-a [in-progress] next: T2 write tests", out)

    def test_stats(self):
        self.repo.write(".claude/memory/journal/2026-11-06-x.md",
                        "---\ndate: 2026-11-06\nkind: failure\ncheck: unit\noutcome: fixed\nattempts: 2\nroot_cause: env\n---\n")
        s = json.loads(self.repo.run("memory.py", "stats", "--since", "2026-11-01").stdout)
        self.assertEqual(s["journal_entries"], 1)
        self.assertEqual(s["by_root_cause"], {"env": 1})
        self.assertEqual(s["mean_attempts"], 2.0)


if __name__ == "__main__":
    unittest.main()
