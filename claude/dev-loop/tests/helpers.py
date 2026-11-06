import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
BIN = PKG / "bin"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


class TempRepo:
    """A throwaway git repository with a `main` branch and one commit."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "test@example.com")
        git(self.root, "config", "user.name", "test")
        self.write("README.md", "x\n")
        self.commit("init")

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def commit(self, msg):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", msg)

    def run(self, script, *args, stdin=None, env=None):
        e = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root))
        e.pop("DEV_LOOP_MODE", None)
        e.update(env or {})
        return subprocess.run([sys.executable, "-I", str(BIN / script), *args], cwd=self.root, input=stdin,
                              capture_output=True, text=True, env=e)

    def hook(self, tool, tool_input, env=None):
        r = self.run("guard.py", stdin=json.dumps({"tool_name": tool, "tool_input": tool_input, "cwd": str(self.root)}), env=env)
        assert r.returncode == 0, r.stderr
        if not r.stdout.strip():
            return None
        out = json.loads(r.stdout)["hookSpecificOutput"]
        return out["permissionDecision"], out["permissionDecisionReason"]

    def close(self):
        self._tmp.cleanup()
