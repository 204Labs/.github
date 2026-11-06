# dev-loop: self-directing, self-correcting development for Claude Code

A small, reusable layer for 204Labs repositories. Claude takes an approved objective from plan to
verified result, corrects its own failures at their cause, records what it learned in the
project's repository, and reviews that learning weekly. It stays inside the approved scope and
under the owner's control.

It holds no project knowledge. Each project keeps its own memory, verification profile and policy.

## What is in the package

| Part | File | Role |
| --- | --- | --- |
| Skill `objective` | `skills/objective/` | Scope gate, plan with measurable criteria, execute, verify, correct, review, learn, report |
| Skill `verify` | `skills/verify/` | Run the project's checks, root-cause protocol, three-correction limit, evidence |
| Skill `learn` | `skills/learn/` | Journal entries, lessons with status, promotion by review only |
| Skill `dream` | `skills/dream/` | Weekly read-only reflection: proposals backed by evidence |
| Agent `dev-loop-reviewer` | `agents/` | Independent read-only review against the acceptance criteria |
| `guard.py` | `bin/` | PreToolUse hook: deny secrets and default-branch pushes; ask before deployments, production data, money, destructive actions and safeguard edits; read-only mode |
| `verify.py` | `bin/` | Selects checks by changed paths, runs them, records evidence, enforces the attempt limit, integrity check against weakened tests |
| `memory.py` | `bin/` | Lints memory (status, evidence, size budgets, no secrets or personal data), session-start summary, weekly stats |
| `install.py` | `bin/` | Installs into a project with a checksum lock; `--check` detects local edits |

## How it maps to the principles

The design follows three ideas: context engineering, persistent memory, and consolidation
away from the work ("dreaming").

- **Context engineering.** Little is loaded every session: a short CLAUDE.md, a short memory
  index and a few lines of session summary. Lessons are read by scope when a task touches
  their area. Promoted rules go into path-scoped `.claude/rules/` files that load only with
  matching files. Subagents get file pointers, not history.
- **Persistent memory, in layers.** Episodic: one journal file per failure or correction.
  Semantic: `LESSONS.md` with an explicit status (verified, confirmed by the owner,
  assumption, temporary, superseded). Procedural: rules, checks and skills, changed only
  through reviewed pull requests. Memory is committed to the project, so it survives
  ephemeral cloud containers, is reviewable, and can be reverted.
- **Dreaming.** A separate, scheduled, read-only pass replays the week's episodes, CI runs,
  reviews and transcripts. It finds recurring failures, contradictions, stale knowledge and
  missing checks, and writes proposals with evidence. Adoption is a human merge.

## Native Claude Code features used, not rebuilt

| Need | Native feature | What dev-loop adds |
| --- | --- | --- |
| Keep going until done | `/goal` (Stop-hook evaluator) | A condition template tied to the objective file |
| Repeat in-session | `/loop` | Nothing; e.g. `/loop 30m /verify` while CI runs |
| Unattended runs | Cloud scheduled tasks (`/schedule`) | The `dream` skill and read-only guard mode |
| React to CI and reviews | PR activity subscription | Nothing; the objective loop handles the events |
| Shared instructions | CLAUDE.md with `@` imports, `.claude/rules/` | A memory index imported by CLAUDE.md |
| Personal memory | Auto memory (machine-local) | Left on. It does not survive cloud containers, hence the committed project memory |
| Delegation | Subagents | One read-only reviewer |
| Safety | Permission rules, hooks | A guard hook; deny rules for secret files |

Why copies instead of a plugin: cloud sessions do not reliably load plugins from an external
marketplace, and these projects are built in cloud sessions. `install.py` copies the package,
and the lock file pins each file's checksum so drift is visible. Plugin packaging can be added
when cloud sessions load org marketplaces.

## Adopt it in a project

1. From this repository: `python3 claude/dev-loop/bin/install.py --target ../<project>`.
2. Merge `templates/settings.hooks.json` into the project's `.claude/settings.json`.
3. Write `.claude/dev-loop/verify.json` so it mirrors the project's CI: checks, path rules,
   required tools. Add project rules to `.claude/dev-loop/policy.json`.
4. Add a short `CLAUDE.md` that imports the project's existing agent instructions and
   `@.claude/memory/MEMORY.md`.
5. Seed `LESSONS.md` only with lessons that have evidence.
6. Run `python3 .claude/dev-loop/bin/verify.py --all` and open a pull request for review.
7. Optional: a weekly cloud scheduled task that runs `/dream` (see the Lurniq example).

To update: change the package here, run its tests, reinstall into each project, and review the
diff there.

## Controls

- **Scope.** The objective skill's scope gate. Owner decisions stay in the project's decision log.
- **Attempts.** Three failed corrections per check per objective, then BLOCKED (exit code 3).
  Only a person can reset the ledger.
- **Tests.** The integrity check fails on new skip or suppression markers and deleted tests,
  and flags removed assertions and changes to safeguards or CI. Waivers need a person.
- **Approval.** The guard asks before deployments, production database changes, financial
  actions, destructive Git or file operations, version tags, and edits to its own safeguards.
  It denies secret files and default-branch pushes in every permission mode.
- **Unattended runs.** Read-only mode allows writes only under `.claude/memory/` and pushes
  only to `claude/dream-*`. It cannot be left in the same clone.
- **History.** Everything is committed on branches and merged by people; rollback is `git revert`.
- **Separation.** Memory, policy and verify profile live in each project; this package has none.
- **Honesty.** Results distinguish PASS from INCOMPLETE (a check could not run). Reports
  separate what was verified from what was assessed.

The guard matches text patterns. It is a safety net, not a sandbox: branch protection,
CODEOWNERS and human review remain the real controls.

## Tests

```sh
python3 -I -m unittest discover -s claude/dev-loop/tests -p "test_*.py"
```
