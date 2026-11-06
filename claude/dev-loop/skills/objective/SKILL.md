---
name: objective
description: Take an approved development objective from plan to verified result without step-by-step prompting - read the requirements, write a plan with measurable acceptance criteria, do the work, verify it with evidence, correct failures (at most three attempts each), get an independent review, record lessons. Use when the user gives a development goal ("implement", "fix", "add tests for", "work on") or asks to resume an open objective.
argument-hint: "<objective in a sentence> | resume [objective id]"
---

# Objective loop

You own the objective from plan to evidence. Do the next step yourself; come back to the
person only for what is theirs to decide. The loop never widens what was approved.

## 0. Scope gate (before anything else)

An objective is approved when the person asked for it in this session, when an open file in
`.claude/objectives/` records the approval, or when the project's decision log names it.

Stop and ask, in one bundled message, when the work would need any of these:

- a product feature, screen, copy, flow or roadmap item the approved documents do not contain;
- a change to an acceptance criterion, requirement or approved design;
- anything the project's own instructions reserve for the owner (read them: for Lurniq,
  AGENTS.md and the Build Handover Section 6);
- a deployment, production data change, payment, new paid service, new data category,
  destructive action, or a change to the dev-loop safeguards.

Continue with the independent approved parts while you wait. Subtasks you invent inside the
approved objective are yours to create: tests, refactors the change needs, docs it touches.

`resume`: read the open objective file, its progress log and the latest verify evidence, then
continue from the first unchecked task. Re-run the scope gate; approval does not stretch.

## 1. Orient (load little, load the right things)

1. The project's entry instructions (CLAUDE.md and what it imports) are already loaded.
2. `.claude/memory/LESSONS.md`: read the lessons whose Scope matches the files or area you will
   touch. `grep -ril <area> .claude/memory/journal` for past failures there.
3. The requirement, decision and design sources for this area, through the project's index.
   Read sections, not whole folders. Quote the requirement IDs you rely on.
4. The existing code and tests for the area. Note what already exists so you reuse it.

## 2. Plan

Copy `template.md` (next to this file) to `.claude/objectives/YYYY-MM-DD-<slug>.md` and fill it:

- **Acceptance criteria**: observable and measurable, each with the evidence that will prove it
  (a check id from the verify profile, a test name, a command and its expected output, a
  screenshot). "Works", "clean" and "improved" are not criteria.
- **Tasks** in dependency order, small enough to verify one at a time.
- **Out of scope** and **owner questions**, so drift is visible.

Mirror the tasks with TaskCreate. Set `status: in-progress` and commit the plan with the work.
Do not wait for plan approval unless the person asked to see the plan first.

If the person wants unattended persistence in this session, suggest they set
`/goal Objective <id> in .claude/objectives/<file> shows status verified or blocked, or stop after 40 turns`.
`/goal`'s evaluator reads only the transcript, so end each step with the line
`Objective <id>: status <status>; next <task>`.

## 3. Execute

One task in progress at a time, in order. Work on a feature branch, never the default branch.
Commit at each coherent step with a message that says why. Tick the task in the objective file
and add one progress-log line: what changed and what evidence exists now.

Delegate only bounded, independent work (normally at most two or three subagents, each given
exact files, criteria and a verification command). Subagents never make owner decisions.

## 4. Verify, correct, repeat

Use the `verify` skill. In short: `python3 .claude/dev-loop/bin/verify.py --objective <id>`, plus
the objective's own evidence (run the behaviour, not only the unit tests).

On a failure: find the cause, fix the cause, re-run. The runner counts failures per check per
objective. After the third failed correction it answers BLOCKED: stop, write a journal entry with
`outcome: blocked`, set the objective to `blocked`, and report the blocker, the evidence and the
options. Never weaken a test, skip a check, suppress a legitimate error or edit a criterion to get
a pass.

## 5. Independent review

Run the `dev-loop-reviewer` agent when the change touches production code, security, privacy,
data handling, the build or CI, or exceeds about 100 changed lines. Give it the objective file
path and the base ref, not your conclusions. Treat each confirmed finding as a failure: fix,
re-verify, and log it. A finding you reject needs a one-line reason in the progress log.

## 6. Learn

Use the `learn` skill: a journal entry for every failure, correction and review finding; a lesson
when something is reusable; a proposed rule only when it is evidence-backed and recurring.

## 7. Close and report

Set the objective status: `verified` only when every criterion has passing evidence from this
HEAD; otherwise `blocked`, or `in-progress` with the reason. Report in this shape:

```
Objective <id>: <status>
Verified (evidence from this run): AC1 ... (check `id`, exit 0, log path) ...
Assessed (my judgement, not mechanically verified): ...
Not verified here, and why (tool or environment unavailable): ...
Needs the owner: ...
Lessons recorded: L-NNN (status), journal/<file>
```

UNAVAILABLE checks are never reported as passed. CI on the pull request is the final word.
