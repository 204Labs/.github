#!/usr/bin/env python3
"""dev-loop guard: a Claude Code PreToolUse hook.

Reads the hook input (JSON) on stdin and answers with a permission decision:

- deny: never allowed, in any permission mode (secrets, pushing to the default branch,
  wiping the repository, leaving read-only mode, writes during a read-only run);
- ask:  a person must approve this exact call (deployments, production databases,
  financial and destructive actions, edits to the safeguards themselves);
- nothing: the normal permission rules apply.

The guard only adds restrictions; it never answers "allow". Rules are text patterns, so
they are a safety net, not a sandbox: branch protection, CODEOWNERS and review stay the
real controls. Project additions live in .claude/dev-loop/policy.json.

Read-only mode (scheduled, unattended runs): `guard.py --enter-readonly` writes a marker in
the clone's .git directory. From then on the session may write only to the policy's
`readonly_writable` paths and push only to `readonly_push_branches`. There is no command to
leave it; the guard denies anything that touches the marker. A fresh clone starts normal.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_POLICY = {
    "default_branches": ["main", "master"],
    "secret_globs": [
        ".env", ".env.*", ".dev.vars", "*.jks", "*.keystore", "*.p12", "*.pfx", "*.pem", "*.key",
        "id_rsa*", "id_ed25519*", "google-services.json", "GoogleService-Info.plist",
        "*.mobileprovision", "local.properties", "credentials.json", "service-account*.json",
    ],
    "secret_allow": [".env.example", "*.pub"],
    # Files the dev loop's own rules live in. Lock-listed files are added automatically.
    "protected_globs": [
        ".claude/settings.json", ".claude/settings.local.json", ".claude/hooks/**",
        ".claude/dev-loop/**",
    ],
    "safe_delete_globs": [
        "build", "build/**", "**/build", "**/build/**", ".gradle/**", "**/.gradle/**",
        "node_modules", "node_modules/**", "**/node_modules", "**/node_modules/**",
        "dist/**", "out/**", "coverage/**", "**/__pycache__", "**/__pycache__/**",
        ".pytest_cache/**", ".tooling/tmp/**", "/tmp/**",
    ],
    "extra_ask_bash": [],   # [{"id": "...", "pattern": "regex", "reason": "..."}]
    "extra_deny_bash": [],
    "extra_ask_mcp": [],    # [{"id": "...", "pattern": "regex on the tool name", "reason": "..."}]
    # MCP tools whose first name word is a write verb need approval, except on these servers.
    "mcp_write_allowed_servers": ["github", "claude-artifacts", "Claude_Docs", "Figma"],
    # Exceptions inside the allowed servers that still need approval.
    "mcp_ask_tools": [
        "mcp__github__delete_file", "mcp__github__create_repository", "mcp__github__fork_repository",
        "mcp__github__actions_run_trigger", "mcp__github__enable_pr_auto_merge",
    ],
    "readonly_writable": [".claude/memory/**", "/tmp/**"],
    "readonly_push_branches": ["claude/dream-*"],
}

WRITE_VERBS = {
    "create", "delete", "update", "put", "patch", "apply", "execute", "deploy", "merge", "reset",
    "rebase", "restore", "pause", "unpause", "promote", "rollback", "buy", "purchase", "send",
    "reply", "forward", "trash", "mark", "revoke", "activate", "assign", "approve", "complete",
    "start", "cancel", "invalidate", "upload", "edit", "add", "remove", "set", "run", "kill",
    "stop", "transfer", "issue", "replace", "stage", "accept", "authorize", "write", "push",
    "label", "unlabel", "archive", "unarchive", "fire", "switch", "request", "rerequest",
    "enable", "disable", "resolve", "unresolve", "sub", "upsert", "copy", "move", "pin",
    "unpin", "declare", "test",
}
FINANCIAL = re.compile(r"(stripe|payment|payout|refund|charge|invoice|billing|purchase|buy_)", re.I)

ASK_BASH = [
    ("deploy-cloudflare", r"\bwrangler\s+(deploy|publish|versions\s+deploy|rollback|delete|secret\s+(put|delete|bulk)|d1\s+(execute|migrations\s+apply)\b.*--remote|r2\s+(bucket|object)\s+(create|put|delete)|kv\b.*\b(put|delete)\b|queues\s+(create|delete))",
     "Cloudflare deployment or production resource change"),
    ("deploy-supabase", r"\bsupabase\s+(db\s+(push|reset)|migration\s+(up|repair)|functions\s+deploy|secrets\s+(set|unset)|projects\s+(create|delete)|link|branches\s+(create|delete))",
     "Supabase deployment or production database change"),
    ("deploy-hosting", r"\b(vercel|netlify|firebase|flyctl|fly|heroku|amplify)\b[^|;&]*\b(deploy|--prod|promote|rollback|release)\b",
     "hosting deployment"),
    ("deploy-infra", r"\b(terraform\s+(apply|destroy|import)|pulumi\s+(up|destroy)|kubectl\s+(apply|create|delete|scale|rollout|patch)|helm\s+(install|upgrade|uninstall|rollback))\b",
     "infrastructure change"),
    ("deploy-cloud-cli", r"\b(aws|gcloud|az)\s+[^|;&]*\b(create|delete|deploy|put|update|terminate|remove|set-iam)\b",
     "cloud account change"),
    ("publish-package", r"\b(npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b|\bcargo\s+publish\b|\bgradlew?\b[^|;&]*\b(publish\w*|bundleRelease|assembleRelease|uploadArchives)\b",
     "package or release publication"),
    ("release-github", r"\bgh\s+(release|repo\s+(delete|edit|rename|archive)|secret|variable|workflow\s+run)\b",
     "GitHub release, repository or secret change"),
    ("financial-cli", r"\bstripe\s+", "financial action"),
    ("history-destructive", r"\bgit\s+(reset\s+--hard|clean\s+-\w*f|checkout\s+--\s|checkout\s+\.(\s|$)|restore\s+(--staged\s+)?(\.|:/)(\s|$)|branch\s+-D\b|stash\s+(drop|clear)|filter-branch|filter-repo|update-ref\s+-d|reflog\s+expire|gc\s+--prune)",
     "destructive Git operation"),
    ("disk-destructive", r"(^|\s)(dd|mkfs(\.\w+)?|shred|fdisk|wipefs)\s", "destructive disk operation"),
    ("pipe-to-shell", r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z)?sh\b", "running a downloaded script"),
]

REDIRECT = re.compile(r"(?:^|[^0-9&>])>>?\s*([^\s&|;<>]+)")
WRITE_MARKERS = re.compile(
    r"(\bsed\s+(-\w+\s+)*-i|\bperl\s+(-\w+\s+)*-i|\btee\b|\bmv\b|\bcp\b|\brm\b|\bchmod\b|\btruncate\b|"
    r"\bln\b|\bgit\s+(checkout|restore|rm|mv|apply|am)\b|\binstall\.py\b|\bpatch\b|\bunlink\b|\btouch\b|"
    r"--reset-ledger|--waive|open\([^)]*['\"][wa])"
)
READONLY_MARKER = "dev-loop/readonly"


def repo_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env).resolve()
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
        return Path(out.stdout.strip()).resolve()
    except (OSError, subprocess.CalledProcessError):
        return Path.cwd().resolve()


def git_dir(root: Path) -> Path:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--absolute-git-dir"],
                             capture_output=True, text=True, check=True)
        return Path(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return root / ".git"


def load_policy(root: Path) -> dict:
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    path = root / ".claude" / "dev-loop" / "policy.json"
    if path.is_file():
        extra = json.loads(path.read_text(encoding="utf-8"))
        for key, value in extra.items():
            if key.startswith("_"):
                continue
            if isinstance(value, list) and isinstance(policy.get(key), list) and not key.startswith("replace_"):
                policy[key] = policy[key] + [v for v in value if v not in policy[key]]
            else:
                policy[key] = value
    lock = root / ".claude" / "dev-loop" / "dev-loop.lock.json"
    if lock.is_file():
        try:
            policy["protected_globs"] += list(json.loads(lock.read_text(encoding="utf-8")).get("files", {}))
        except ValueError:
            pass
    return policy


def rel(path: str, root: Path, cwd: Path) -> str:
    """Repository-relative POSIX path, or an absolute path when outside the repository."""
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = cwd / p
    p = Path(os.path.normpath(str(p)))
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def matches(path: str, globs: list[str]) -> bool:
    name = path.rsplit("/", 1)[-1]
    for g in globs:
        if "/" in g:
            if fnmatch.fnmatchcase(path, g) or (g.endswith("/**") and path == g[:-3]):
                return True
        elif fnmatch.fnmatchcase(name, g):
            return True
    return False


def is_secret(path: str, policy: dict) -> bool:
    return matches(path, policy["secret_globs"]) and not matches(path, policy["secret_allow"])


def segments(command: str) -> list[str]:
    return [s.strip() for s in re.split(r"&&|\|\||;|\n|(?<!\|)\|(?!\|)", command) if s.strip()]


def words(segment: str) -> list[str]:
    try:
        return shlex.split(segment, comments=False)
    except ValueError:
        return segment.split()


def git_push_targets(seg: str) -> tuple[list[str], bool, bool]:
    """Destination branch names of a `git push`, whether it forces, whether it deletes."""
    w = words(seg)
    try:
        i = w.index("push")
    except ValueError:
        return [], False, False
    if "git" not in w[:i]:
        return [], False, False
    args = w[i + 1:]
    force = any(a in ("-f", "--force", "--force-with-lease", "--force-if-includes") or a.startswith("--force") for a in args)
    delete = any(a in ("-d", "--delete") for a in args)
    positional = [a for a in args if not a.startswith("-")]
    refs = positional[1:] if positional else []
    targets = []
    for ref in refs:
        if ref.startswith("+"):
            force = True
            ref = ref[1:]
        if ref.startswith(":"):
            delete = True
        dest = ref.split(":", 1)[-1]
        targets.append(dest.removeprefix("refs/heads/"))
    return targets, force, delete


def current_branch(root: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), "branch", "--show-current"], capture_output=True, text=True)
        return out.stdout.strip()
    except OSError:
        return ""


def decide(data: dict, root: Path, policy: dict, readonly: bool) -> tuple[str, str, str] | None:
    tool = data.get("tool_name", "")
    tin = data.get("tool_input") or {}
    cwd = Path(data.get("cwd") or root)

    # File tools ---------------------------------------------------------------------
    path_value = tin.get("file_path") or tin.get("notebook_path") or tin.get("path")
    if tool in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit") and path_value:
        p = rel(path_value, root, cwd)
        if is_secret(p, policy):
            return "deny", "secret-file", f"{p} holds secrets or signing material; agents never read or write it"
        if tool != "Read":
            if READONLY_MARKER in p or p.endswith("/" + READONLY_MARKER):
                return "deny", "readonly-marker", "the read-only marker cannot be changed by the agent"
            if readonly and not matches(p, policy["readonly_writable"]):
                return "deny", "readonly-write", f"read-only run: writing {p} is not allowed (only {', '.join(policy['readonly_writable'])})"
            if matches(p, policy["protected_globs"]):
                return "ask", "safeguard-edit", f"{p} is a dev-loop safeguard; a person must approve changes to it"
        return None

    # Shell ---------------------------------------------------------------------------
    if tool == "Bash":
        command = tin.get("command", "")
        if READONLY_MARKER in command or "DEV_LOOP_MODE" in command:
            if "--enter-readonly" not in command:
                return "deny", "readonly-marker", "the read-only marker and mode cannot be changed by the agent"
        for rule in policy.get("extra_deny_bash", []):
            if re.search(rule["pattern"], command):
                return "deny", rule["id"], rule.get("reason", "denied by project policy")
        decision = None
        # Rules about pipelines see the whole command; segments() splits on "|".
        for rule_id, pattern, reason in ASK_BASH:
            if "|" in pattern and re.search(pattern, command):
                decision = ("ask", rule_id, reason)
        for seg in segments(command):
            w = words(seg)
            # secrets named on the command line
            for token in w:
                if token.startswith("-") or "=" in token and not token.startswith(("./", "/")):
                    continue
                p = rel(token, root, cwd)
                if is_secret(p, policy) and (root / p if not p.startswith("/") else Path(p)).exists():
                    return "deny", "secret-file", f"{p} holds secrets or signing material; agents never read or write it"
            # git push
            targets, force, delete = git_push_targets(seg)
            if "push" in w and "git" in w:
                bare = not targets
                hits_default = any(t in policy["default_branches"] or t == "HEAD" and current_branch(root) in policy["default_branches"] for t in targets)
                if bare and current_branch(root) in policy["default_branches"]:
                    hits_default = True
                if hits_default:
                    return "deny", "push-default-branch", "pushing to the default branch is never done by the agent; open a pull request"
                if readonly:
                    dest = targets or [current_branch(root)]
                    if not all(any(fnmatch.fnmatchcase(t, g) for g in policy["readonly_push_branches"]) for t in dest):
                        return "deny", "readonly-push", f"read-only run: pushes go only to {', '.join(policy['readonly_push_branches'])}"
                if "--tags" in w or any(re.fullmatch(r"v\d[\w.\-]*", t) for t in targets):
                    decision = decision or ("ask", "release-tag", "pushing a version tag starts a release")
                if force or delete:
                    decision = decision or ("ask", "push-rewrite", "force push or remote branch deletion rewrites shared history")
            # rm -r / -f
            if w and w[0] in ("rm", "sudo") and ("rm" in w):
                flags = "".join(a[1:] for a in w if a.startswith("-") and not a.startswith("--"))
                long_flags = [a for a in w if a.startswith("--")]
                if "r" in flags.lower() or "f" in flags or "--recursive" in long_flags or "--force" in long_flags:
                    targets_rm = [a for a in w[w.index("rm") + 1:] if not a.startswith("-")]
                    if any(t in ("/", "~", "*", ".", "..", "/*", "~/") or rel(t, root, cwd) in ("", ".") for t in targets_rm):
                        return "deny", "rm-root", "recursive delete of a root, home or repository directory"
                    unsafe = [t for t in targets_rm if not matches(rel(t, root, cwd), policy["safe_delete_globs"])]
                    if unsafe:
                        decision = decision or ("ask", "rm-recursive", f"recursive or forced delete of {', '.join(unsafe[:3])}")
            # database clients pointed at a non-local host
            if w and Path(w[0]).name in ("psql", "pg_dump", "pg_restore", "mysql", "mongosh", "redis-cli"):
                hosts = re.findall(r"(?:-h\s*|--host[=\s]|host=|://(?:[^@/\s]+@)?)([^\s:/?'\"]+)", seg)
                if any(h not in ("localhost", "127.0.0.1", "::1") and not h.startswith("/") for h in hosts):
                    decision = decision or ("ask", "remote-database", "database command against a non-local host")
            for rule_id, pattern, reason in ASK_BASH:
                if re.search(pattern, seg):
                    decision = decision or ("ask", rule_id, reason)
            for rule in policy.get("extra_ask_bash", []):
                if re.search(rule["pattern"], seg):
                    decision = decision or ("ask", rule["id"], rule.get("reason", "needs approval under project policy"))
            # writes into protected or (read-only) non-writable paths
            mentioned = [rel(t, root, cwd) for t in w if not t.startswith("-")]
            redirects = [rel(t, root, cwd) for t in REDIRECT.findall(seg) if t not in ("/dev/null", "/dev/stderr", "/dev/stdout")]
            writes = bool(WRITE_MARKERS.search(seg)) or bool(redirects)
            if writes and any(matches(p, policy["protected_globs"]) for p in mentioned + redirects):
                if readonly:
                    return "deny", "readonly-write", "read-only run: dev-loop safeguards cannot be changed"
                decision = decision or ("ask", "safeguard-edit", "this command changes a dev-loop safeguard; a person must approve it")
            if readonly:
                inside = [p for p in redirects if not p.startswith("/") or p.startswith(str(root))]
                if any(not matches(p, policy["readonly_writable"]) for p in inside):
                    return "deny", "readonly-write", "read-only run: redirecting output into the repository is not allowed"
                if re.search(r"\b(sed|perl)\s+(-\w+\s+)*-i|\bgit\s+(apply|am|checkout\s+--|restore|rm|mv|reset|rebase|merge|cherry-pick|stash)\b|\binstall\.py\b", seg):
                    return "deny", "readonly-write", "read-only run: this command changes tracked files or history"
                if re.search(r"(^|\s)(rm|mv|cp|tee|truncate|touch|ln)\s", " " + seg):
                    targets_w = [p for p in mentioned[1:] if not p.startswith("/") or p.startswith(str(root))]
                    if any(not matches(p, policy["readonly_writable"]) and (root / p).exists() for p in targets_w if not p.startswith("/")):
                        return "deny", "readonly-write", "read-only run: this command changes repository files"
        return decision

    # MCP tools -----------------------------------------------------------------------
    if tool.startswith("mcp__"):
        parts = tool.split("__")
        server = parts[1] if len(parts) > 2 else ""
        name = parts[-1]
        first = name.replace("-", "_").split("_")[0].lower()
        is_write = first in WRITE_VERBS
        if FINANCIAL.search(tool) and (is_write or server.lower() == "stripe"):
            return ("deny" if readonly else "ask"), "financial", "financial action"
        if readonly and is_write:
            if tool == "mcp__github__create_branch" or tool.startswith("mcp__github__create_pull_request"):
                branch = str(tin.get("branch") or tin.get("head") or "")
                if any(fnmatch.fnmatchcase(branch, g) for g in policy["readonly_push_branches"]) and tin.get("draft", True) is not False:
                    return None
            return "deny", "readonly-mcp", f"read-only run: {name} changes something outside the session"
        for rule in policy.get("extra_ask_mcp", []):
            if re.search(rule["pattern"], tool):
                return "ask", rule["id"], rule.get("reason", "needs approval under project policy")
        if tool in policy["mcp_ask_tools"]:
            return "ask", "mcp-sensitive", f"{name} needs approval"
        if is_write and server not in policy["mcp_write_allowed_servers"]:
            return "ask", "mcp-write", f"{server} {name} changes an outside service; a person must approve it"
    return None


def log(root: Path, decision: tuple[str, str, str], tool: str) -> None:
    try:
        d = git_dir(root) / "dev-loop"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "guard.log", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "decision": decision[0],
                                 "rule": decision[1], "tool": tool}) + "\n")
    except OSError:
        pass


def main(argv: list[str]) -> int:
    root = repo_root()
    if "--enter-readonly" in argv:
        marker = git_dir(root) / READONLY_MARKER
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n", encoding="utf-8")
        print(f"dev-loop: read-only mode on for this clone ({marker})")
        return 0
    if "--status" in argv:
        ro = (git_dir(root) / READONLY_MARKER).exists() or os.environ.get("DEV_LOOP_MODE") == "readonly"
        print("dev-loop guard: " + ("read-only" if ro else "normal"))
        return 0
    try:
        data = json.load(sys.stdin)
        policy = load_policy(root)
        readonly = (git_dir(root) / READONLY_MARKER).exists() or os.environ.get("DEV_LOOP_MODE") == "readonly"
        decision = decide(data, root, policy, readonly)
    except Exception as exc:  # fail towards a person, never silently open
        decision = ("ask", "guard-error", f"dev-loop guard could not evaluate this call ({type(exc).__name__})")
        data = {}
    if decision:
        log(root, decision, data.get("tool_name", "?"))
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision[0],
            "permissionDecisionReason": f"dev-loop guard [{decision[1]}]: {decision[2]}",
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
