#!/usr/bin/env python3
"""dev-loop install: copy the shared package into a project and pin it.

  python3 <package>/bin/install.py --target <project>   install or update (a person runs this)
  python3 .claude/dev-loop/bin/install.py --check       in the project: installed files match the lock

What is installed and pinned (overwritten on update, never edited in the project):
  skills/<name>/  -> .claude/skills/<name>/
  agents/*.md     -> .claude/agents/
  bin/*.py        -> .claude/dev-loop/bin/

What is scaffolded once and then owned by the project (never overwritten, never pinned):
  templates/project/** -> the same paths in the project (memory, objectives, verify profile, policy)

Why copies and not a plugin: Claude Code cloud sessions do not reliably load plugins from an
external marketplace, and these projects are built in cloud sessions. The lock keeps one
source of truth upstream and makes any local change to a safeguard visible.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

LOCK = Path(".claude/dev-loop/dev-loop.lock.json")
HOOK_COMMANDS = {
    "PreToolUse": "python3 \"$CLAUDE_PROJECT_DIR/.claude/dev-loop/bin/guard.py\"",
    "SessionStart": "python3 \"$CLAUDE_PROJECT_DIR/.claude/dev-loop/bin/memory.py\" session-start",
}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def package_files(pkg: Path) -> dict[str, Path]:
    """Installed path -> source file."""
    out: dict[str, Path] = {}
    for src in sorted((pkg / "skills").rglob("*")):
        if src.is_file():
            out[(Path(".claude/skills") / src.relative_to(pkg / "skills")).as_posix()] = src
    for src in sorted((pkg / "agents").glob("*.md")):
        out[(Path(".claude/agents") / src.name).as_posix()] = src
    for src in sorted((pkg / "bin").glob("*.py")):
        out[(Path(".claude/dev-loop/bin") / src.name).as_posix()] = src
    return out


def source_ref(pkg: Path) -> str:
    def git(*a):
        r = subprocess.run(["git", "-C", str(pkg), *a], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    remote, commit = git("config", "--get", "remote.origin.url"), git("rev-parse", "HEAD")
    dirty = " (uncommitted changes)" if git("status", "--porcelain", "--", ".") else ""
    return f"{remote.split('@')[-1]}@{commit[:12]}{dirty}" if commit else "local copy"


def hooks_wired(target: Path) -> list[str]:
    missing = []
    settings = target / ".claude" / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8")) if settings.is_file() else {}
    for event, cmd in HOOK_COMMANDS.items():
        cmds = [h.get("command", "") for g in data.get("hooks", {}).get(event, []) for h in g.get("hooks", [])]
        if cmd not in cmds:
            missing.append(event)
    return missing


def install(pkg: Path, target: Path) -> int:
    version = (pkg / "VERSION").read_text(encoding="utf-8").strip()
    files = package_files(pkg)
    lock_path = target / LOCK
    old = json.loads(lock_path.read_text(encoding="utf-8")).get("files", {}) if lock_path.is_file() else {}
    for dest in sorted(set(old) - set(files)):
        p = target / dest
        if p.is_file() and sha(p) == old[dest]:
            p.unlink()
            print(f"removed  {dest} (no longer in the package)")
    for dest, src in files.items():
        p = target / dest
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.is_file() and dest in old and sha(p) != old[dest]:
            print(f"WARNING  {dest} was edited in the project; the package version replaces it")
        shutil.copy2(src, p)
    for src in sorted((pkg / "templates" / "project").rglob("*")):
        if src.is_file():
            dest = target / src.relative_to(pkg / "templates" / "project")
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                print(f"created  {dest.relative_to(target)} (project-owned; edit freely)")
    lock = {"package": "204Labs dev-loop", "version": version, "source": source_ref(pkg),
            "note": "Installed by install.py. Do not edit these files here; change the package upstream and reinstall.",
            "files": {d: sha(target / d) for d in sorted(files)}}
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    print(f"installed dev-loop {version} ({len(files)} files) from {lock['source']}")
    missing = hooks_wired(target)
    if missing:
        print("\nWire these hooks into .claude/settings.json (see templates/settings.hooks.json):")
        for event in missing:
            print(f"  {event}: {HOOK_COMMANDS[event]}")
    return 0


def check(target: Path) -> int:
    lock_path = target / LOCK
    if not lock_path.is_file():
        print(f"lock: {LOCK} missing")
        return 1
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    problems = []
    for dest, digest in lock["files"].items():
        p = target / dest
        if not p.is_file():
            problems.append(f"missing  {dest}")
        elif sha(p) != digest:
            problems.append(f"modified {dest} (differs from dev-loop {lock['version']})")
    problems += [f"hook not wired: {e}" for e in hooks_wired(target)]
    for line in problems:
        print(f"lock: {line}")
    print(f"lock: dev-loop {lock['version']} from {lock['source']}: " + ("ok" if not problems else f"{len(problems)} problem(s)"))
    return 1 if problems else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=Path, help="project root to install into")
    ap.add_argument("--check", action="store_true", help="verify the installed copy against its lock")
    args = ap.parse_args(argv)
    if args.check:
        target = args.target or Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                                                     text=True).stdout.strip() or ".")
        return check(target.resolve())
    if not args.target:
        ap.error("--target is required to install")
    pkg = Path(__file__).resolve().parent.parent
    if not (pkg / "VERSION").is_file():
        ap.error("run install.py from the package (claude/dev-loop/bin), not from an installed copy")
    return install(pkg, args.target.resolve())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
