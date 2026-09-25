"""Install / remove the skimread hook in Claude Code settings, print snippets."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Tuple

from . import store
from .hooks import skimread_exe

MARK = "skimread hook"


def settings_path(scope: str) -> Path:
    if scope == "project":
        return Path.cwd() / ".claude" / "settings.json"
    if scope == "local":
        return Path.cwd() / ".claude" / "settings.local.json"
    return Path.home() / ".claude" / "settings.json"


def _load(p: Path) -> Dict[str, Any]:
    if p.exists():
        try:
            return json.loads(p.read_text() or "{}")
        except ValueError as e:
            raise SystemExit(f"skimread: {p} is not valid JSON ({e}); fix it or remove it first")
    return {}


def hook_entry(exe: str, wrap: str) -> Dict[str, Any]:
    matcher = "Read" if wrap == "off" else "Bash|Read"
    return {"matcher": matcher, "hooks": [{"type": "command", "command": f"{exe} hook", "timeout": 30}]}


def _strip_ours(settings: Dict[str, Any]) -> None:
    hooks = settings.get("hooks") or {}
    pre = hooks.get("PreToolUse") or []
    hooks["PreToolUse"] = [g for g in pre if not any(MARK in (h.get("command") or "") for h in (g.get("hooks") or []))]
    if not hooks["PreToolUse"]:
        hooks.pop("PreToolUse", None)
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    perms = settings.get("permissions") or {}
    allow = perms.get("allow") or []
    allow = [a for a in allow if "skimread" not in a]
    if allow:
        perms["allow"] = allow
    else:
        perms.pop("allow", None)
    if perms:
        settings["permissions"] = perms
    else:
        settings.pop("permissions", None)


def install(scope: str = "global", wrap: str = "noisy", uninstall: bool = False) -> Tuple[Path, str]:
    p = settings_path(scope)
    settings = _load(p)
    _strip_ours(settings)
    exe = skimread_exe()
    msg = ""
    if not uninstall:
        settings.setdefault("hooks", {}).setdefault("PreToolUse", []).append(hook_entry(exe, wrap))
        if wrap != "off":
            allow = settings.setdefault("permissions", {}).setdefault("allow", [])
            rule = f"Bash({exe}:*)"
            if rule not in allow:
                allow.append(rule)
        cfg = store.load_config()
        cfg["wrap"] = wrap
        store.save_config(cfg)
        msg = (f"Installed skimread hook into {p}\n"
               f"  Read guard: files over {cfg['read_threshold_lines']} lines get an outline first\n"
               f"  Bash wrap:  {wrap}" + ("" if wrap == "off" else f"  (allow rule added: Bash({exe}:*))") +
               "\nRestart Claude Code for the hook to take effect. Verify with: skimread stats")
    else:
        msg = f"Removed skimread hook and permission rule from {p}"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(settings, indent=2) + "\n")
    return p, msg


def snippet(wrap: str = "noisy") -> str:
    exe = skimread_exe()
    s: Dict[str, Any] = {"hooks": {"PreToolUse": [hook_entry(exe, wrap)]}}
    if wrap != "off":
        s["permissions"] = {"allow": [f"Bash({exe}:*)"]}
    return json.dumps(s, indent=2)


def agents_snippet() -> str:
    exe = skimread_exe()
    return f"""## Reading files and running commands (skimread)

Before reading a file longer than ~300 lines, run `{exe} outline <file>` and then read
only the line ranges you need (`{exe} read <file> START-END`, or your editor's ranged read).

Run noisy commands (tests, builds, installs, linters, diffs) through skimread so you get a
short, lossless digest instead of thousands of lines:

    {exe} run -c "pytest -q"
    {exe} run -c "npm test"

The digest ends with an id. `{exe} show <id>` prints the full output, `{exe} show <id> 120-180`
prints a slice, and `{exe} show <id> --grep "Error" -C 3` searches it.
"""


def mcp_snippet() -> str:
    exe = skimread_exe()
    parts = exe.split(" ", 1)
    cmd = parts[0]
    args = (parts[1].split() if len(parts) > 1 else []) + ["mcp"]
    return json.dumps({"mcpServers": {"skimread": {"command": cmd, "args": args}}}, indent=2)
