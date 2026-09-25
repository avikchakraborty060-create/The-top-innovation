"""Claude Code PreToolUse hook: the Read guard and the Bash wrapper.

Input arrives as JSON on stdin (see https://code.claude.com/docs/en/hooks).
Output is JSON on stdout. Returning None means "no opinion": the tool call
proceeds exactly as Claude issued it.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from typing import Any, Dict, Optional

from . import store
from .outline import analyze, is_binary_path, render_outline
from .tokens import estimate_tokens, fmt_tokens

# Commands whose output is usually long and mostly noise. Used by wrap=noisy.
NOISY = {
    "npm", "npx", "yarn", "pnpm", "bun", "bunx", "node_modules/.bin", "jest", "vitest", "mocha", "cypress",
    "playwright", "tsc", "eslint", "prettier", "webpack", "vite", "next", "nuxt", "ng", "expo", "turbo", "nx",
    "pytest", "py.test", "tox", "nox", "ruff", "mypy", "pyright", "black", "flake8", "pylint", "isort",
    "pip", "pip3", "uv", "poetry", "pipx", "conda", "python", "python3",
    "cargo", "rustc", "rustup", "go", "gofmt", "golangci-lint", "make", "cmake", "ninja", "gcc", "g++", "clang",
    "clang++", "ld", "meson", "bazel", "buck2", "gradle", "gradlew", "mvn", "mvnw", "sbt", "lein", "dotnet",
    "msbuild", "swift", "xcodebuild", "flutter", "dart", "mix", "bundle", "rake", "rspec", "rails", "composer",
    "artisan", "php", "zig", "deno", "docker", "docker-compose", "podman", "kubectl", "helm", "terraform",
    "tofu", "pulumi", "ansible", "ansible-playbook", "vagrant", "brew", "apt", "apt-get", "dnf", "yum",
    "find", "tree", "fd", "rg", "grep", "ag", "du", "ls", "git", "gh", "ctest", "stack", "cabal", "ghc",
}
# git subcommands worth wrapping (others are short or interactive)
GIT_NOISY = {"diff", "log", "show", "status", "blame", "grep", "ls-files"}

_DANGEROUS = re.compile(
    r"(?:^|[\s;&|(])(?:rm|rmdir|sudo|su|mv|cp|chmod|chown|chgrp|dd|mkfs|shred|truncate|kill|killall|pkill|"
    r"reboot|shutdown|crontab|systemctl|launchctl|ssh|scp|rsync|curl|wget|nc|ncat|telnet|eval|exec|"
    r"xargs|tee|ln|passwd|useradd|apt-get\s+remove|npm\s+publish|cargo\s+publish|twine|"
    r"gh\s+(?:pr|release|repo)\s+(?:create|merge|delete|edit)|git\s+(?:push|reset|checkout|switch|rebase|clean|"
    r"stash|commit|merge|cherry-pick|revert|am|apply|filter-branch|branch\s+-[dD]|tag\s+-d|rm|mv|restore|"
    r"remote|config))(?:\s|$)"
)
_SHELL_STATE = re.compile(r"(?:^|&&|;|\|\||\n)\s*(?:export|source|\.|alias|unset|set|ulimit|shopt|trap|cd)\s")
_INTERACTIVE = re.compile(r"(?:^|\s)(?:vim?|nvim|nano|emacs|less|more|top|htop|tmux|screen|watch|man|claude|codex|"
                          r"python3?|node|irb|psql|mysql|sqlite3|ipython)(?:\s*$|\s+-i\b)|\s-it\b|\s-ti\b")
_REDIRECT = re.compile(r"(?<![0-9&])>(?!&)|&>|>>")
_CD_PREFIX = re.compile(r"^\s*(cd\s+(?:\"[^\"]*\"|'[^']*'|\S+)\s*&&\s*)")


def skimread_exe() -> str:
    exe = shutil.which("skimread")
    if not exe and sys.argv and sys.argv[0].endswith("skimread") and os.path.isabs(sys.argv[0]):
        exe = sys.argv[0]
    if exe:
        return exe
    return f"{shlex.quote(sys.executable)} -m skimread"


def _first_word(cmd: str) -> str:
    try:
        toks = shlex.split(cmd, posix=True)
    except ValueError:
        toks = cmd.split()
    while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
        toks.pop(0)
    while toks and toks[0] in ("time", "nice", "env", "uvx", "npx", "pnpx", "bunx"):
        toks.pop(0)
    if len(toks) >= 2 and toks[0] in ("uv", "poetry", "pipenv", "pdm", "hatch") and toks[1] == "run":
        toks = toks[2:]
    if not toks:
        return ""
    base = toks[0].rsplit("/", 1)[-1]
    if base in ("python", "python3", "py"):
        # only wrap python when it is running a known noisy module or a test file
        if len(toks) >= 3 and toks[1] == "-m" and toks[2] in ("pytest", "unittest", "pip", "build", "mypy", "ruff", "tox", "nox"):
            return toks[2]
        if len(toks) >= 2 and re.search(r"test", toks[1]):
            return "pytest"
        return "python-script"
    if base == "git":
        sub = next((t for t in toks[1:] if not t.startswith("-")), "")
        return f"git {sub}"
    return base


def should_wrap(cmd: str, mode: str) -> bool:
    if mode == "off" or not cmd.strip():
        return False
    if "skimread" in cmd or "SKIMREAD" in cmd:
        return False
    if cmd.rstrip().endswith("&") or cmd.rstrip().endswith("\\"):
        return False
    body = _CD_PREFIX.sub("", cmd, count=1)
    if _SHELL_STATE.search(body) or _DANGEROUS.search(body) or _INTERACTIVE.search(body) or _REDIRECT.search(body):
        return False
    if len(cmd) > 4000:
        return False
    fw = _first_word(body)
    if mode == "all":
        return bool(fw)
    if fw.startswith("git "):
        return fw.split(" ", 1)[1] in GIT_NOISY
    if fw == "ls":
        return "-R" in body or "-lR" in body or "-Ra" in body
    if fw == "python-script":
        return False
    return fw in NOISY


def wrap_command(cmd: str, exe: Optional[str] = None) -> str:
    exe = exe or skimread_exe()
    m = _CD_PREFIX.match(cmd)
    prefix = m.group(1) if m else ""
    body = cmd[len(prefix):] if m else cmd
    return f"{prefix}{exe} run -c {shlex.quote(body)}"


def bash_hook(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ti = payload.get("tool_input") or {}
    cmd = ti.get("command") or ""
    if ti.get("run_in_background"):
        return None
    cfg = store.load_config()
    if not should_wrap(cmd, str(cfg.get("wrap", "noisy"))):
        return None
    new_cmd = wrap_command(cmd)
    try:
        import subprocess
        bash = shutil.which("bash")
        if not bash:
            return None  # no bash (e.g. Windows): leave the command alone
        chk = subprocess.run([bash, "-n", "-c", new_cmd], capture_output=True, timeout=5)
        if chk.returncode != 0:
            return None
    except Exception:
        return None
    updated = dict(ti, command=new_cmd)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": updated}}


MAX_OUTLINE_BYTES = 8 * 1024 * 1024


def read_hook(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ti = payload.get("tool_input") or {}
    path = ti.get("file_path") or ""
    if not path or not os.path.isfile(path) or is_binary_path(path):
        return None
    cfg = store.load_config()
    session = str(payload.get("session_id") or "default")
    key = os.path.abspath(path)
    state = store.session_state(session)
    guarded: Dict[str, Any] = state.setdefault("guarded", {})
    explicit = ("offset" in ti and ti["offset"] not in (None, "")) or ("limit" in ti and ti["limit"] not in (None, ""))

    if explicit:
        if key in guarded:
            g = guarded[key]
            limit = ti.get("limit")
            try:
                n = int(limit) if limit not in (None, "") else max(0, int(g.get("lines", 0)) - int(ti.get("offset") or 0))
            except (TypeError, ValueError):
                n = 0
            tok = int(n * g.get("tok_per_line", 10))
            store.log_event({"kind": "read_slice", "path": key, "lines": n, "tokens": tok, "session": session})
        return None

    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size < int(cfg["read_threshold_chars"]) and size < int(cfg["read_threshold_lines"]):
        return None  # too small in bytes to reach either threshold
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_OUTLINE_BYTES + 1)
    except OSError:
        return None
    truncated = len(data) > MAX_OUTLINE_BYTES
    text = data[:MAX_OUTLINE_BYTES].decode("utf-8", errors="replace")
    if "\x00" in text[:4096]:
        return None  # binary
    n_lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    if n_lines < int(cfg["read_threshold_lines"]) and size < int(cfg["read_threshold_chars"]):
        return None

    tokens = estimate_tokens(text)
    if key in guarded:
        # second full read of the same file in this session: let it through
        store.log_event({"kind": "read_full", "path": key, "tokens": tokens, "session": session})
        return None

    o = analyze(path, text)
    body = render_outline(o, max_entries=70, show_path=False)
    if truncated:
        body += "\n  (outline covers the first 8 MB only)"
    guarded[key] = {"lines": n_lines, "tokens": tokens, "tok_per_line": tokens / max(1, n_lines),
                    "outline_tokens": estimate_tokens(body)}
    store.save_session_state(session, state)
    store.log_event({"kind": "read_guard", "path": key, "lines": n_lines, "tokens": tokens,
                     "outline_tokens": estimate_tokens(body), "session": session})
    reason = (
        f"skimread: {path} is {n_lines:,} lines (~{fmt_tokens(tokens)} tokens). Read only the part you need: "
        f"call Read again with offset and limit chosen from this outline. Any Read that sets offset or limit "
        f"is allowed; repeating this exact full read is also allowed if you really need the whole file.\n"
        f"Outline: {body}"
    )
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def handle(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if payload.get("hook_event_name") not in (None, "PreToolUse"):
        return None
    tool = payload.get("tool_name")
    if tool == "Bash":
        return bash_hook(payload)
    if tool == "Read":
        return read_hook(payload)
    return None
