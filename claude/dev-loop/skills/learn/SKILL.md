---
name: learn
description: Record what a session learned in the project's committed memory - a journal entry for each failure, correction, owner correction or review finding, and a lesson (with status verified, confirmed, assumption, temporary or superseded) when it is reusable. Use after fixing a failure, when the owner corrects you or states a preference, after a review, and when an approach fails and should not be repeated.
argument-hint: "[what happened]"
---

# Learn

Memory lives in the project repository, so it survives ephemeral containers, is reviewed in
pull requests and can be reverted. Each project has its own; never copy one project's memory
into another.

| Layer | File | What goes in |
| --- | --- | --- |
| Episodic | `.claude/memory/journal/YYYY-MM-DD-<slug>.md` | One event: failure, correction, review finding, owner correction. Facts and evidence. |
| Semantic | `.claude/memory/LESSONS.md` | Reusable knowledge, one entry per lesson, with a status. |
| Procedural | `.claude/rules/*.md`, skills, the verify profile | Rules that change how work is done. Only through a reviewed pull request. |
| Index | `.claude/memory/MEMORY.md` | Loaded every session. Pointers and the few lessons every task needs. |

Owner decisions belong in the project's decision log through its normal process. Memory may
point to a decision ID; it never restates or reinterprets one.

## 1. Journal entry (always, for every failure, correction or finding)

Copy `journal-template.md` (next to this file). Fill the frontmatter (`kind`, `check`,
`outcome`, `attempts`, `root_cause`) so the weekly review can count. Write the symptom, the
cause with evidence, the fix, and the verification that proved the fix.

## 2. Lesson (when someone would benefit next time)

Add or update an entry in LESSONS.md:

```
## L-NNN: short imperative title
- Type: mistake | solution | avoid | preference | decision | environment
- Status: verified | confirmed | assumption | temporary | superseded by L-NNN
- Scope: paths, area or tool it applies to
- Rule: one or two sentences someone can act on
- Evidence: journal file, commit, test name, CI run, or the owner's words with date
- Seen: N (dates)
- Updated: YYYY-MM-DD
- Expires: YYYY-MM-DD   (temporary only)
```

Status rules:

- `verified`: reproduced and fixed, with evidence a reader can check.
- `confirmed`: the owner said it (quote and date). Owner corrections and preferences are confirmed.
- `assumption`: believed, not proven. Say what would prove it.
- `temporary`: true for now (an outage, a pending decision); needs `Expires`.
- `superseded by L-NNN` or a decision ID: kept for history, no longer followed.

Before adding, search LESSONS.md: update `Seen` and `Evidence` on the existing entry rather
than adding a duplicate. When new evidence contradicts a lesson, supersede it; never edit history away.

## 3. Promote (only with evidence, only by review)

A lesson becomes a rule when it is `verified` or `confirmed` and either seen twice or important
enough that the owner agrees. Propose it in the same pull request as:

- a path-scoped rule in `.claude/rules/<area>.md` (`paths:` frontmatter), which loads only
  when matching files are touched, so it costs nothing elsewhere; or
- a check in the verify profile, when a machine can catch it; or
- a skill, when it is a repeatable procedure.

Keep CLAUDE.md short. Never add to it what a path-scoped rule or a check can carry. Mark the
lesson `Promoted: <file>` so the weekly review does not propose it again.

## Never record

Secrets, tokens, keys, passwords, personal data, real names, real messages or family content,
production data, or security finding details. `memory.py lint` blocks the obvious patterns;
you are responsible for the rest.

Run `python3 .claude/dev-loop/bin/memory.py lint` after editing memory.
