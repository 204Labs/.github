---
name: verify
description: Verify a change with evidence and correct failures at their cause - runs the project's verification profile (build, lint, types, tests, security, integrity), interprets the result, applies the root-cause protocol, and stops after three failed corrections. Use after any code change, before saying work is done, and whenever a check or CI run fails.
argument-hint: "[--objective id] [--all | --only check,check]"
allowed-tools: Bash(python3 .claude/dev-loop/bin/verify.py *), Bash(python3 .claude/dev-loop/bin/memory.py *)
---

# Verify and correct

## Run

From the repository root:

```sh
python3 .claude/dev-loop/bin/verify.py --list                    # what this change selects
python3 .claude/dev-loop/bin/verify.py --objective <id>          # run it
python3 .claude/dev-loop/bin/verify.py --objective <id> --all    # everything, before a PR
```

The checks and when they run are in `.claude/dev-loop/verify.json`, which mirrors the
project's CI. Results: PASS, INCOMPLETE (a check could not run here), FAIL, BLOCKED.
Evidence is in `.git/dev-loop/runs/<time>/summary.json`, with one log per check.

Unit tests are not the whole story. Also produce the evidence the objective's criteria name,
from the running behaviour:

- web UI: drive the page with Playwright (pre-installed), assert the interaction, and collect
  `console` errors and failed requests; an unexpected console error is a failure;
- Android UI: the Robolectric screen tests and screenshot evidence the project already has; a
  device or emulator run only where the project's CI policy allows it;
- server or API: run it locally and call it (status, headers, body), plus the test suite;
- CLI or tool: run it on a real input and check the output.

## When a check fails: root-cause protocol

1. Read the log (`notes` and the log path). Quote the first real error, not the last line.
2. Reproduce it in the smallest command you can (one test, one file).
3. State the cause in one sentence, with the evidence. "Flaky" is a hypothesis: prove it by
   running the unchanged test again; if it passes alone and fails in the suite, find the shared
   state.
4. Fix the cause. If the test is wrong about the requirement, show the requirement text and fix
   the test to match it; that is the only reason a test changes.
5. Re-run the failing check, then the whole selection.
6. Log it with the `learn` skill (a journal entry, kind `failure`).

Environment failures (network limits, missing SDK, rate limits) are causes too: fix the
environment or report the check UNAVAILABLE. Never present them as code passes.

## Never

- weaken, skip, delete or loosen a test, assertion, lint rule or threshold to get a pass;
- add `@Ignore`, `.skip`, `|| true`, `continue-on-error`, `@ts-ignore`, `noqa` or a broad
  suppression (the integrity check fails on these unless a person approved a waiver);
- change an acceptance criterion, the verify profile or CI so that a failure disappears;
- reset the attempt ledger or waive an integrity rule yourself (the guard asks a person);
- call something verified because it "should" work.

## Attempt limit

The runner counts failed runs per check per objective. The first failure is detection; each
later failure is a used correction. After the third failed correction it answers BLOCKED and
exits with code 3. Then stop changing code for that failure. Report: the failing check, the
cause as far as you know it, the three things you tried and why each failed, and the options.
Only a person can reset the ledger (`--reset-ledger <check>`).

## Security checks

Run the security checks the profile has (secret scan, dependency scan, static analysis) when
their tools are installed. Where the container cannot run one, say so and leave it to CI. Never
paste secrets, tokens or personal data into logs, memory, commits or reports.

## Reporting

Copy the summary lines into the objective's progress log, or a pull request comment headed
"Local verification evidence". Separate what the runner verified from what you assessed.
