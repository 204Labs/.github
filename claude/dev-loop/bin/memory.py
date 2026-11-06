#!/usr/bin/env python3
"""dev-loop memory: lint the project's committed memory, summarise it at session start,
and count what happened for the weekly review.

Layout (in the project, committed, one project per repository):

  .claude/memory/MEMORY.md         index, imported by CLAUDE.md, loaded every session (short)
  .claude/memory/LESSONS.md        semantic memory: curated lessons with a status
  .claude/memory/journal/*.md      episodic memory: one file per failure, correction or review
  .claude/memory/dreams/*.md       weekly reflection reports (proposals, never rules)
  .claude/objectives/*.md          approved objectives with plan, criteria and evidence

Commands:
  memory.py lint            exit 1 when the memory breaks its own rules
  memory.py session-start   a few lines of context for a SessionStart hook
  memory.py stats [--since YYYY-MM-DD]   counts for the dream report
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

TYPES = {"preference", "mistake", "solution", "avoid", "decision", "environment"}
STATUSES = {"confirmed", "verified", "assumption", "temporary", "superseded"}
NEEDS_EVIDENCE = {"confirmed", "verified"}
OBJ_STATUSES = {"proposed", "planned", "in-progress", "verifying", "blocked", "verified", "done", "abandoned"}
OBJ_OPEN = {"proposed", "planned", "in-progress", "verifying", "blocked"}
JOURNAL_KINDS = {"failure", "correction", "review", "decision-request", "note"}
JOURNAL_OUTCOMES = {"fixed", "blocked", "open", "n/a"}
DEFAULT_LIMITS = {"memory_index_lines": 80, "claude_md_lines": 60, "lesson_rule_chars": 400, "lessons_max": 60}
SECRET_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS access key"), (r"gh[pousr]_[A-Za-z0-9]{30,}", "GitHub token"),
    (r"\bsk-[A-Za-z0-9_-]{20,}", "API secret key"), (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"xox[abprs]-[A-Za-z0-9-]{10,}", "Slack token"), (r"eyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.", "JWT"),
    (r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"]?[^\s'\"<>]{8,}", "credential assignment"),
]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_OK = re.compile(r"@(example\.(com|org|net)|noreply\.|users\.noreply\.github\.com|anthropic\.com$)")
FIELD = re.compile(r"^- (\w[\w-]*):\s*(.*)$")


def root() -> Path:
    try:
        return Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                                   check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return Path.cwd()


def limits(r: Path) -> dict:
    lim = dict(DEFAULT_LIMITS)
    prof = r / ".claude" / "dev-loop" / "verify.json"
    if prof.is_file():
        lim.update(json.loads(prof.read_text(encoding="utf-8")).get("memory", {}))
    return lim


def frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    out = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip()
    return out


def parse_lessons(text: str) -> list[dict]:
    lessons, cur = [], None
    for n, line in enumerate(text.splitlines(), 1):
        h = re.match(r"^## (L-\d{3,}):?\s*(.*)$", line)
        if h:
            cur = {"id": h.group(1), "title": h.group(2).strip(), "line": n}
            lessons.append(cur)
            continue
        if line.startswith("## "):
            cur = None
            continue
        f = FIELD.match(line)
        if cur is not None and f:
            cur[f.group(1).lower()] = f.group(2).strip()
    return lessons


def lint(r: Path) -> list[str]:
    errors = []
    mem = r / ".claude" / "memory"
    lim = limits(r)
    if not mem.is_dir():
        return [f"{mem} missing (run install.py)"]
    idx = mem / "MEMORY.md"
    if not idx.is_file():
        errors.append("MEMORY.md missing")
    elif len(idx.read_text(encoding="utf-8").splitlines()) > lim["memory_index_lines"]:
        errors.append(f"MEMORY.md is over {lim['memory_index_lines']} lines: it loads every session; move detail to LESSONS.md")
    cm = r / "CLAUDE.md"
    if cm.is_file() and len(cm.read_text(encoding="utf-8").splitlines()) > lim["claude_md_lines"]:
        errors.append(f"CLAUDE.md is over {lim['claude_md_lines']} lines; keep it short and link out")
    lessons_file = mem / "LESSONS.md"
    lessons = parse_lessons(lessons_file.read_text(encoding="utf-8")) if lessons_file.is_file() else []
    ids = [l["id"] for l in lessons]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"LESSONS.md: {dup} used twice")
    if len([l for l in lessons if l.get("status") != "superseded"]) > lim["lessons_max"]:
        errors.append(f"LESSONS.md has over {lim['lessons_max']} live lessons; consolidate in the next dream")
    today = dt.date.today()
    for l in lessons:
        where = f"LESSONS.md:{l['line']} {l['id']}"
        for key in ("type", "status", "rule", "evidence", "updated"):
            if not l.get(key):
                errors.append(f"{where}: missing '- {key.capitalize()}:'")
        if l.get("type") and l["type"] not in TYPES:
            errors.append(f"{where}: type '{l['type']}' not one of {sorted(TYPES)}")
        status = l.get("status", "").split()[0] if l.get("status") else ""
        if status and status not in STATUSES:
            errors.append(f"{where}: status '{status}' not one of {sorted(STATUSES)}")
        if status in NEEDS_EVIDENCE and l.get("evidence", "").lower() in ("", "none", "-", "n/a"):
            errors.append(f"{where}: a {status} lesson needs evidence")
        if status == "superseded" and not re.search(r"L-\d{3,}|[A-Z]+-\d{3}", l.get("status", "") + l.get("superseded-by", "")):
            errors.append(f"{where}: say what supersedes it ('superseded by L-NNN' or a decision ID)")
        if status == "temporary":
            exp = l.get("expires", "")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", exp):
                errors.append(f"{where}: a temporary lesson needs '- Expires: YYYY-MM-DD'")
        if len(l.get("rule", "")) > lim["lesson_rule_chars"]:
            errors.append(f"{where}: rule over {lim['lesson_rule_chars']} characters; say it in one or two sentences")
    for j in sorted((mem / "journal").glob("*.md")) if (mem / "journal").is_dir() else []:
        if j.name == "README.md":
            continue
        fm = frontmatter(j.read_text(encoding="utf-8"))
        for key in ("date", "kind", "outcome"):
            if key not in fm:
                errors.append(f"journal/{j.name}: frontmatter missing '{key}'")
        if fm.get("kind") and fm["kind"] not in JOURNAL_KINDS:
            errors.append(f"journal/{j.name}: kind '{fm['kind']}' not one of {sorted(JOURNAL_KINDS)}")
        if fm.get("outcome") and fm["outcome"] not in JOURNAL_OUTCOMES:
            errors.append(f"journal/{j.name}: outcome '{fm['outcome']}' not one of {sorted(JOURNAL_OUTCOMES)}")
    for o in sorted((r / ".claude" / "objectives").glob("*.md")) if (r / ".claude" / "objectives").is_dir() else []:
        if o.name == "README.md":
            continue
        fm = frontmatter(o.read_text(encoding="utf-8"))
        if fm.get("status") not in OBJ_STATUSES:
            errors.append(f"objectives/{o.name}: status '{fm.get('status')}' not one of {sorted(OBJ_STATUSES)}")
        if not fm.get("approved"):
            errors.append(f"objectives/{o.name}: 'approved:' must say who approved the objective and where")
    scan = [p for p in [r / "CLAUDE.md"] if p.is_file()] + sorted(mem.rglob("*.md")) + \
        (sorted((r / ".claude" / "objectives").glob("*.md")) if (r / ".claude" / "objectives").is_dir() else [])
    for p in scan:
        text = p.read_text(encoding="utf-8")
        for pattern, what in SECRET_PATTERNS:
            if re.search(pattern, text):
                errors.append(f"{p.relative_to(r)}: looks like a {what}; memory never holds secrets")
        for e in EMAIL.findall(text):
            if not EMAIL_OK.search(e):
                errors.append(f"{p.relative_to(r)}: email address {e[:3]}...; memory holds no personal data")
    return errors


def objectives(r: Path) -> list[tuple[str, str, str]]:
    out = []
    d = r / ".claude" / "objectives"
    for o in sorted(d.glob("*.md")) if d.is_dir() else []:
        if o.name == "README.md":
            continue
        text = o.read_text(encoding="utf-8")
        fm = frontmatter(text)
        if fm.get("status") in OBJ_OPEN:
            nxt = next((l.strip()[6:] for l in text.splitlines() if l.strip().startswith("- [ ] ")), "no open task listed")
            out.append((fm.get("id", o.stem), fm.get("status", "?"), nxt))
    return out


def session_start(r: Path) -> str:
    lines = []
    mem = r / ".claude" / "memory"
    lessons = parse_lessons((mem / "LESSONS.md").read_text(encoding="utf-8")) if (mem / "LESSONS.md").is_file() else []
    counts = {}
    for l in lessons:
        s = (l.get("status") or "?").split()[0]
        counts[s] = counts.get(s, 0) + 1
    live = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
    lines.append(f"dev-loop memory: {len(lessons)} lessons ({live or 'none'}) in .claude/memory/LESSONS.md; "
                 "read the ones whose scope matches your task before you start.")
    today = dt.date.today().isoformat()
    expired = [l["id"] for l in lessons if (l.get("status") or "").startswith("temporary") and l.get("expires", "9999") < today]
    if expired:
        lines.append(f"Expired temporary lessons (do not rely on them; recheck or supersede): {', '.join(expired)}")
    objs = objectives(r)
    if objs:
        lines.append("Open objectives (resume only within their approved scope):")
        lines += [f"  {i} [{s}] next: {n}" for i, s, n in objs[:5]]
    try:
        gd = Path(subprocess.run(["git", "-C", str(r), "rev-parse", "--absolute-git-dir"], capture_output=True,
                                 text=True, check=True).stdout.strip()) / "dev-loop"
        ledger = json.loads((gd / "ledger.json").read_text(encoding="utf-8")) if (gd / "ledger.json").is_file() else {}
        blocked = [f"{o}/{c}" for o, cs in ledger.items() for c, e in cs.items() if e.get("failures", 0) > 3]
        if blocked:
            lines.append(f"Blocked checks waiting for a person: {', '.join(blocked)}")
        if (gd / "readonly").exists():
            lines.append("Mode: READ-ONLY run. Write only under .claude/memory/; push only to claude/dream-* branches.")
    except (OSError, subprocess.CalledProcessError, ValueError):
        pass
    return "\n".join(lines)


def stats(r: Path, since: str) -> dict:
    mem = r / ".claude" / "memory"
    entries = []
    for j in sorted((mem / "journal").glob("*.md")) if (mem / "journal").is_dir() else []:
        fm = frontmatter(j.read_text(encoding="utf-8"))
        if fm.get("date", "") >= since:
            entries.append(fm | {"file": j.name})
    by = lambda k: {v: sum(1 for e in entries if e.get(k) == v) for v in sorted({e.get(k, "?") for e in entries})}
    attempts = [int(e["attempts"]) for e in entries if e.get("attempts", "").isdigit()]
    lessons = parse_lessons((mem / "LESSONS.md").read_text(encoding="utf-8")) if (mem / "LESSONS.md").is_file() else []
    return {"since": since, "journal_entries": len(entries), "by_kind": by("kind"), "by_outcome": by("outcome"),
            "by_root_cause": by("root_cause"), "by_check": by("check"),
            "mean_attempts": round(sum(attempts) / len(attempts), 2) if attempts else None,
            "lessons_by_status": {s: sum(1 for l in lessons if (l.get("status") or "").startswith(s)) for s in sorted(STATUSES)},
            "lessons_seen_twice_or_more": [l["id"] for l in lessons if re.match(r"\s*([2-9]|\d{2,})", l.get("seen", ""))],
            "open_objectives": [o[0] for o in objectives(r)], "files": [e["file"] for e in entries]}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("lint")
    sub.add_parser("session-start")
    st = sub.add_parser("stats")
    st.add_argument("--since", default=(dt.date.today() - dt.timedelta(days=7)).isoformat())
    args = ap.parse_args(argv)
    r = root()
    if args.cmd == "lint":
        errors = lint(r)
        for e in errors:
            print(f"memory: {e}")
        print("memory: ok" if not errors else f"memory: {len(errors)} problem(s)")
        return 1 if errors else 0
    if args.cmd == "session-start":
        try:
            print(session_start(r))
        except Exception as exc:  # a broken summary must never stop a session
            print(f"dev-loop memory summary unavailable ({type(exc).__name__}); read .claude/memory/MEMORY.md")
        return 0
    print(json.dumps(stats(r, args.since), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
