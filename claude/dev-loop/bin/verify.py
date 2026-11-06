#!/usr/bin/env python3
"""dev-loop verify: run the checks a change needs, keep evidence, enforce the attempt limit.

The project's checks live in .claude/dev-loop/verify.json (see templates/verify.json). Each
check runs when a changed file matches `when` and no `unless`, or always. A check whose tool
or environment is missing is UNAVAILABLE, never PASS. Results:

  PASS        every selected check passed
  INCOMPLETE  nothing failed, but at least one selected check could not run here
  FAIL        a check failed; fix the cause and run again
  BLOCKED     a check has failed on 1 + max_corrections runs for this objective: stop and
              report the blocker to a person (exit code 3)

Evidence (command, exit code, duration, log) is written under .git/dev-loop/runs/, which is
never committed. The attempt ledger is .git/dev-loop/ledger.json; resetting it or waiving an
integrity rule needs a person's approval (the guard asks).

Built-in checks: integrity (no weakened tests, safeguard changes flagged), memory
(memory.py lint) and lock (install.py --check).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXIT = {"PASS": 0, "INCOMPLETE": 2, "FAIL": 1, "BLOCKED": 3}

# Added lines matching these fail the integrity check: they switch a test or check off.
DEFAULT_SKIP_MARKERS = [
    r"@Ignore\b", r"@Disabled\b", r"\b(it|test|describe)\.(skip|only|todo)\(", r"\bx(it|describe|test)\(",
    r"@pytest\.mark\.(skip|xfail)", r"@unittest\.(skip|expectedFailure)", r"\bassumeTrue\(\s*false",
    r"\bcontinue-on-error:\s*true", r"-x\s+test\b", r"--exclude-task\s+test", r"//\s*ktlint-disable",
    r"[#/]\s*nosemgrep", r"\bgitleaks:allow\b",
]
# Added lines matching these are reported for the reviewer: sometimes right, never silent.
DEFAULT_SUPPRESS_MARKERS = [
    r"@Suppress\(", r"@SuppressWarnings\(", r"@SuppressLint\(", r"eslint-disable", r"@ts-(ignore|expect-error)",
    r"#\s*type:\s*ignore", r"#\s*noqa", r"\|\|\s*true\b", r"catch\s*\([^)]*\)\s*\{\s*\}",
]
WAIVER = re.compile(r"dev-loop:\s*allow\s+([A-Z][A-Z0-9-]+)")


def sh(args: list[str], cwd: Path, check: bool = False) -> str:
    out = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and out.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def repo_root() -> Path:
    return Path(sh(["git", "rev-parse", "--show-toplevel"], Path.cwd(), check=True).strip())


def git_dir(root: Path) -> Path:
    return Path(sh(["git", "rev-parse", "--absolute-git-dir"], root, check=True).strip())


def merge_base(root: Path, base: str) -> str | None:
    for ref in (base, base.split("/", 1)[-1]):
        out = subprocess.run(["git", "merge-base", ref, "HEAD"], cwd=root, capture_output=True, text=True)
        if out.returncode == 0:
            return out.stdout.strip()
    return None


def changed_files(root: Path, mb: str | None) -> list[str]:
    files: set[str] = set()
    if mb:
        files.update(sh(["git", "diff", "--name-only", mb], root).split())
    files.update(sh(["git", "diff", "--name-only", "HEAD"], root).split())
    files.update(sh(["git", "ls-files", "--others", "--exclude-standard"], root).split())
    return sorted(files)


def deleted_files(root: Path, mb: str | None) -> list[str]:
    files: set[str] = set()
    for ref in ([mb] if mb else []) + ["HEAD"]:
        files.update(sh(["git", "diff", "--name-only", "--diff-filter=D", ref], root).split())
    return sorted(files)


def added_and_removed_lines(root: Path, mb: str | None) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    added, removed = [], []
    diff = sh(["git", "diff", "-U0", "--no-color", mb or "HEAD"], root)
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("+") and not line.startswith("+++") and current:
            added.append((current, line[1:]))
        elif line.startswith("-") and not line.startswith("---") and current:
            removed.append((current, line[1:]))
    for path in sh(["git", "ls-files", "--others", "--exclude-standard"], root).split():
        p = root / path
        if p.is_file() and p.stat().st_size < 2_000_000:
            try:
                added += [(path, l) for l in p.read_text(encoding="utf-8").splitlines()]
            except UnicodeDecodeError:
                pass
    return added, removed


def tree_fingerprint(root: Path, mb: str | None) -> str:
    """Hash of the change under test: diff against the base, local edits and untracked files."""
    h = hashlib.sha256()
    for args in (["git", "diff", "--no-color", mb or "HEAD"], ["git", "diff", "--no-color", "HEAD"]):
        h.update(sh(args, root).encode())
    for path in sorted(sh(["git", "ls-files", "--others", "--exclude-standard"], root).split()):
        p = root / path
        if p.is_file():
            h.update(path.encode() + p.read_bytes())
    return h.hexdigest()[:16]


def selected(check: dict, files: list[str], ignore: list[str]) -> bool:
    if check.get("manual"):
        return False
    if check.get("always"):
        return True
    relevant = [f for f in files if not any(re.search(p, f) for p in ignore)]
    if any(re.search(p, f) for f in relevant for p in check.get("also_when", [])):
        return True
    for f in relevant:
        if any(re.search(p, f) for p in check.get("when", [])) and not any(re.search(p, f) for p in check.get("unless", [])):
            return True
    return False


def unmet(check: dict, root: Path) -> list[str]:
    missing = []
    for req in check.get("requires", []):
        if "cmd" in req and not shutil.which(req["cmd"]):
            missing.append(f"command `{req['cmd']}` not installed")
        if "env" in req and not os.environ.get(req["env"]):
            missing.append(f"environment variable {req['env']} not set")
        if "path" in req and not (root / req["path"]).exists():
            missing.append(f"{req['path']} missing")
        if "probe" in req and subprocess.run(req["probe"], shell=True, cwd=root, capture_output=True).returncode != 0:
            missing.append(req.get("why", f"probe failed: {req['probe']}"))
    return missing


def run_cmd(cmd: str, cwd: Path, log: Path, timeout: int) -> tuple[int, float]:
    start = time.time()
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"$ {cmd}\n")
        fh.flush()
        try:
            rc = subprocess.run(cmd, shell=True, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            fh.write(f"\n[dev-loop] timed out after {timeout}s\n")
            rc = 124
    return rc, time.time() - start


def tail(path: Path, n: int = 40) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


# Built-in checks ----------------------------------------------------------------------

def integrity(root: Path, profile: dict, mb: str | None, waived: set[str], log: Path) -> tuple[str, list[str]]:
    """No weakened tests; safeguard changes surfaced for a person."""
    findings, warnings = [], []
    tests = profile.get("test_paths", [])
    markers = DEFAULT_SKIP_MARKERS + profile.get("skip_markers", [])
    soft = DEFAULT_SUPPRESS_MARKERS + profile.get("suppress_markers", [])
    is_test = lambda f: any(re.search(p, f) for p in tests)
    for f in deleted_files(root, mb):
        if is_test(f):
            findings.append(("deleted-test", f"test file deleted: {f}"))
    added, removed = added_and_removed_lines(root, mb)
    scan_all = profile.get("skip_marker_scope", "all") == "all"
    excluded = profile.get("integrity_exclude", [])
    for f, line in added:
        if any(re.search(p, f) for p in excluded) or not (scan_all or is_test(f)):
            continue
        for m in markers:
            if re.search(m, line):
                waiver = WAIVER.search(line)
                if waiver:
                    warnings.append(f"waived in code ({waiver.group(1)}): {f}: {line.strip()[:120]}")
                else:
                    findings.append(("skip-marker", f"{f}: adds `{m}`: {line.strip()[:120]}"))
        for m in soft:
            if re.search(m, line):
                waiver = WAIVER.search(line)
                warnings.append((f"waived in code ({waiver.group(1)}): " if waiver else
                                 "suppression added, reviewer to confirm it is justified: ") + f"{f}: {line.strip()[:120]}")
                break
    assert_re = re.compile(profile.get("assertion_pattern", r"\b(assert\w*|expect|check|verify)\b"))
    for f in sorted({f for f, _ in removed if is_test(f)}):
        r = sum(1 for g, l in removed if g == f and assert_re.search(l))
        a = sum(1 for g, l in added if g == f and assert_re.search(l))
        if r > a:
            warnings.append(f"{f}: {r} assertion lines removed, {a} added; the reviewer must confirm none was weakened")
    safeguards = profile.get("safeguard_paths", []) + [r"^\.claude/dev-loop/", r"^\.claude/settings(\.local)?\.json$", r"^\.github/workflows/"]
    for f in changed_files(root, mb):
        if any(re.search(p, f) for p in safeguards):
            warnings.append(f"safeguard or CI changed, needs a person's review: {f}")
    active = [m for rule, m in findings if f"integrity:{rule}" not in waived]
    waived_msgs = [f"WAIVED by approval: {m}" for rule, m in findings if f"integrity:{rule}" in waived]
    with open(log, "a", encoding="utf-8") as fh:
        for m in active + waived_msgs + warnings:
            fh.write(m + "\n")
    return ("FAIL" if active else "PASS"), active + waived_msgs + [f"warning: {w}" for w in warnings]


def builtin(name: str, root: Path, profile: dict, mb, waived, log: Path) -> tuple[str, list[str]]:
    if name == "integrity":
        return integrity(root, profile, mb, waived, log)
    script = {"memory": [sys.executable, "-I", str(HERE / "memory.py"), "lint"],
              "lock": [sys.executable, "-I", str(HERE / "install.py"), "--check"]}[name]
    with open(log, "a", encoding="utf-8") as fh:
        rc = subprocess.run(script, cwd=root, stdout=fh, stderr=subprocess.STDOUT).returncode
    return ("PASS" if rc == 0 else "FAIL"), ([] if rc == 0 else [tail(log, 15)])


# Ledger -------------------------------------------------------------------------------

def load_ledger(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=".claude/dev-loop/verify.json")
    ap.add_argument("--base", help="base ref (default: profile base, else origin/main)")
    ap.add_argument("--objective", help="objective id for the attempt ledger (default: current branch)")
    ap.add_argument("--only", help="comma-separated check ids")
    ap.add_argument("--all", action="store_true", help="run every non-manual check, whatever changed")
    ap.add_argument("--list", action="store_true", help="show which checks this change selects, run nothing")
    ap.add_argument("--waive", action="append", default=[], help="integrity:<rule>, approved by a person")
    ap.add_argument("--reset-ledger", metavar="CHECK", help="clear a check's failures (a person approves)")
    args = ap.parse_args(argv)

    root = repo_root()
    profile = json.loads((root / args.profile).read_text(encoding="utf-8"))
    gd = git_dir(root) / "dev-loop"
    gd.mkdir(parents=True, exist_ok=True)
    objective = args.objective or os.environ.get("DEV_LOOP_OBJECTIVE") or sh(["git", "branch", "--show-current"], root).strip() or "detached"
    max_corr = int(profile.get("max_corrections", 3))
    ledger_path = gd / "ledger.json"
    ledger = load_ledger(ledger_path)

    if args.reset_ledger:
        ledger.get(objective, {}).pop(args.reset_ledger, None)
        ledger_path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
        print(f"ledger: cleared {args.reset_ledger} for {objective}")
        return 0

    base = args.base or profile.get("base", "origin/main")
    mb = merge_base(root, base)
    files = changed_files(root, mb)
    checks = profile["checks"]
    if args.only:
        wanted = set(args.only.split(","))
        checks = [c for c in checks if c["id"] in wanted]
    elif args.all:
        checks = [c for c in checks if not c.get("manual")]
    else:
        checks = [c for c in checks if selected(c, files, profile.get("ignore", []))]

    head = sh(["git", "rev-parse", "--short", "HEAD"], root).strip()
    print(f"dev-loop verify · objective {objective} · HEAD {head} · base {base} ({(mb or 'not found')[:9]}) · {len(files)} changed file(s)")
    if args.list:
        for c in checks:
            print(f"  would run {c['id']}: {c.get('run') or 'builtin ' + c.get('builtin', '')}")
        return 0

    run_dir = gd / "runs" / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    results = []
    waived = set(args.waive)
    tree = tree_fingerprint(root, mb)
    for c in checks:
        log = run_dir / f"{c['id']}.log"
        log.touch()
        status, notes, duration, rc = "PASS", [], 0.0, 0
        missing = unmet(c, root)
        if missing and c.get("setup"):
            rc, duration = run_cmd(c["setup"], root / c.get("cwd", "."), log, int(c.get("timeout", 1800)))
            missing = unmet(c, root)
        if missing:
            status, notes = "UNAVAILABLE", missing + ([c["fallback"]] if c.get("fallback") else [])
        elif "builtin" in c:
            t0 = time.time()
            status, notes = builtin(c["builtin"], root, profile, mb, waived, log)
            duration = time.time() - t0
        else:
            rc, duration = run_cmd(c["run"], root / c.get("cwd", "."), log, int(c.get("timeout", 1800)))
            if rc != 0:
                status, notes = "FAIL", [tail(log)]
            for after in c.get("after", []):
                run_cmd(after, root, log, 300)

        entry = ledger.setdefault(objective, {}).setdefault(c["id"], {"failures": 0, "resolved": 0})
        unchanged = False
        if status == "FAIL" and entry["failures"] and entry.get("tree") == tree:
            # Nothing changed since the last failure: a rerun, not a correction attempt.
            unchanged = True
            notes = ["no change since the last failure, so this run is not counted as a correction"] + notes
        elif status == "FAIL":
            entry["failures"] += 1
            entry["tree"] = tree
            entry["last_failure"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            entry["fingerprint"] = hashlib.sha256("\n".join(notes).encode()).hexdigest()[:12]
        if status == "FAIL" and entry["failures"] > max_corr:
            status = "BLOCKED"  # stays blocked until a person resets the ledger
        elif status == "PASS" and entry["failures"]:
            entry["resolved"] += 1
            entry["last_resolved_after"] = entry["failures"]
            entry["failures"] = 0
            entry.pop("tree", None)
        corrections_used = max(0, entry["failures"] - 1)
        results.append({"id": c["id"], "status": status, "exit_code": rc, "seconds": round(duration, 1),
                        "command": c.get("run") or f"builtin:{c.get('builtin')}", "log": str(log),
                        "failures": entry["failures"], "corrections_used": corrections_used, "notes": notes})
        mark = {"PASS": "ok  ", "FAIL": "FAIL", "UNAVAILABLE": "n/a ", "BLOCKED": "STOP"}[status]
        extra = (f" (failure {entry['failures']}{', unchanged tree' if unchanged else ''}; "
                 f"corrections left {max(0, max_corr - corrections_used)})") if status == "FAIL" else ""
        print(f"  [{mark}] {c['id']} {round(duration, 1)}s{extra}")
        if status in ("FAIL", "BLOCKED", "UNAVAILABLE"):
            for n in notes:
                print("        " + n.replace("\n", "\n        "))
        elif notes:
            for n in notes:
                print("        " + n)
    ledger_path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")

    statuses = {r["status"] for r in results}
    overall = ("BLOCKED" if "BLOCKED" in statuses else "FAIL" if "FAIL" in statuses
               else "INCOMPLETE" if "UNAVAILABLE" in statuses else "PASS")
    summary = {"objective": objective, "head": head, "base": base, "merge_base": mb, "overall": overall,
               "changed_files": files, "results": results, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"overall: {overall} · evidence {run_dir}/summary.json")
    if overall == "BLOCKED":
        print(f"STOP: a check failed {max_corr + 1} times for objective {objective}. Do not try another fix; "
              "report the blocker, the evidence and the options to a person.")
    if not checks:
        print("note: no check selected; nothing was verified")
    return EXIT[overall]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
