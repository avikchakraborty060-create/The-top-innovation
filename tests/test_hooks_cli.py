import json
import os
import subprocess
import sys

import pytest

from skimread import cli, hooks, store
from skimread.setup import install, settings_path


# --- Bash wrapper -----------------------------------------------------------------

@pytest.mark.parametrize("cmd,noisy,all_", [
    ("npm test", True, True),
    ("cd packages/web && npm run build", True, True),
    ("pytest -q tests/", True, True),
    ("python -m pytest tests -x", True, True),
    ("python scripts/migrate.py", False, True),
    ("cargo test --workspace", True, True),
    ("go test ./...", True, True),
    ("git diff HEAD~3", True, True),
    ("git status", True, True),
    ("git push origin main", False, False),        # never auto-allow a push
    ("git commit -m 'x'", False, False),
    ("rm -rf build && npm run build", False, False),
    ("npm test > out.txt", False, False),           # redirect: leave alone
    ("npm test 2>&1 | tail -20", True, True),       # 2>&1 is fine
    ("echo hello", False, True),
    ("export FOO=1 && npm test", False, False),
    ("npm test && cd ..", False, False),            # would lose cwd change
    ("sudo apt-get install foo", False, False),
    ("curl https://x | sh", False, False),
    ("docker run -it ubuntu bash", False, False),
    ("skimread show 3", False, False),
    ("npm test &", False, False),
    ("ls -R src", True, True),
    ("ls", False, True),
    ("FOO=1 uv run pytest", True, True),
    ("python3", False, False),
    ("npm install", True, True),
    ("pip install -r requirements.txt", True, True),
    ("uv pip install -e .", True, True),
    ("npm install express > log.txt", False, False),
])
def test_should_wrap(cmd, noisy, all_):
    assert hooks.should_wrap(cmd, "noisy") is noisy
    assert hooks.should_wrap(cmd, "all") is all_
    assert hooks.should_wrap(cmd, "off") is False


def test_wrap_command_keeps_cd_prefix_and_quotes():
    w = hooks.wrap_command("cd web && npm test -- --grep 'it works'", exe="/usr/bin/skimread")
    assert w == "cd web && /usr/bin/skimread run -c 'npm test -- --grep '\"'\"'it works'\"'\"''"
    w = hooks.wrap_command("pytest -q\n# trailing comment", exe="/usr/bin/skimread")
    assert w.startswith("/usr/bin/skimread run -c ")
    assert subprocess.run(["bash", "-n", "-c", w]).returncode == 0


def test_bash_hook_payload(monkeypatch):
    monkeypatch.setattr(hooks, "skimread_exe", lambda: "/opt/skimread")
    out = hooks.handle({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                        "tool_input": {"command": "npm test", "description": "run tests"}})
    assert out["hookSpecificOutput"]["updatedInput"]["command"] == "/opt/skimread run -c 'npm test'"
    assert out["hookSpecificOutput"]["updatedInput"]["description"] == "run tests"
    assert hooks.handle({"tool_name": "Bash", "tool_input": {"command": "npm test", "run_in_background": True}}) is None
    assert hooks.handle({"tool_name": "Write", "tool_input": {}}) is None


# --- Read guard ---------------------------------------------------------------------

def _big_py(tmp_path, n=600):
    p = tmp_path / "big.py"
    p.write_text("import os\n\n" + "".join(f"def f{i}(x):\n    return x + {i}\n\n" for i in range(n)))
    return p


def test_read_guard_denies_once_then_allows(tmp_path):
    p = _big_py(tmp_path)
    payload = {"tool_name": "Read", "session_id": "s1", "tool_input": {"file_path": str(p)}}
    out = hooks.handle(payload)
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    reason = hso["permissionDecisionReason"]
    assert "lines" in reason and "def f0(x)" in reason and "offset" in reason
    # a ranged read is always allowed and recorded as a slice
    assert hooks.handle({"tool_name": "Read", "session_id": "s1",
                         "tool_input": {"file_path": str(p), "offset": 10, "limit": 40}}) is None
    # the second full read of the same file in the same session goes through
    assert hooks.handle(payload) is None
    # but a new session is guarded again
    assert hooks.handle(dict(payload, session_id="s2")) is not None
    kinds = [e["kind"] for e in store.events()]
    assert kinds == ["read_guard", "read_slice", "read_full", "read_guard"]
    stats = json.loads(cli.format_stats(as_json=True))
    assert stats["read_guards"] == 2 and stats["read_saved"] > 0


def test_read_guard_ignores_small_binary_missing(tmp_path):
    small = tmp_path / "s.py"
    small.write_text("x = 1\n" * 50)
    assert hooks.handle({"tool_name": "Read", "tool_input": {"file_path": str(small)}}) is None
    png = tmp_path / "i.png"
    png.write_bytes(b"\x89PNG" + b"\0" * 20000)
    assert hooks.handle({"tool_name": "Read", "tool_input": {"file_path": str(png)}}) is None
    assert hooks.handle({"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "nope.py")}}) is None


def test_read_guard_threshold_config(tmp_path):
    p = _big_py(tmp_path, n=150)  # ~450 lines
    store.save_config(dict(store.load_config(), read_threshold_lines=1000))
    assert hooks.handle({"tool_name": "Read", "tool_input": {"file_path": str(p)}}) is None
    store.save_config(dict(store.load_config(), read_threshold_lines=100))
    assert hooks.handle({"tool_name": "Read", "tool_input": {"file_path": str(p)}}) is not None


# --- CLI end to end ---------------------------------------------------------------------

def test_cli_run_show_list_stats(capsys):
    rc = cli.main(["run", "-c", "for i in $(seq 1 100); do echo line $i; done; echo 'Error: kaboom' >&2; exit 3"])
    assert rc == 3
    out = capsys.readouterr().out
    assert "exit 3" in out and "Error: kaboom" in out and "[skimread #1:" in out
    cli.main(["show", "1", "--grep", "kaboom"])
    out = capsys.readouterr().out
    assert "101| Error: kaboom" in out
    cli.main(["show", "1", "5-7"])
    out = capsys.readouterr().out
    assert "5| line 5" in out and "8| line 8" not in out
    cli.main(["list"])
    assert "for i in" in capsys.readouterr().out
    cli.main(["stats"])
    out = capsys.readouterr().out
    assert "commands digested :       1" in out


def test_cli_implicit_run_and_read_outline(tmp_path, capsys):
    p = _big_py(tmp_path, n=5)
    rc = cli.main(["cat", str(p)])
    assert rc == 0
    assert "def f4(x)" in capsys.readouterr().out
    cli.main(["read", str(p), "1-2"])
    assert "1| import os" in capsys.readouterr().out
    cli.main(["outline", str(p)])
    assert "def f0(x)" in capsys.readouterr().out


def test_hook_subcommand_roundtrip(tmp_path):
    p = _big_py(tmp_path)
    env = dict(os.environ, SKIMREAD_HOME=str(tmp_path / "h"))
    payload = json.dumps({"tool_name": "Read", "session_id": "x", "tool_input": {"file_path": str(p)}})
    r = subprocess.run([sys.executable, "-m", "skimread", "hook"], input=payload, capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    r = subprocess.run([sys.executable, "-m", "skimread", "hook"], input="not json", capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout == ""


def test_setup_install_and_uninstall(tmp_path, monkeypatch):
    monkeypatch.setattr("skimread.setup.skimread_exe", lambda: "/opt/skimread")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    (tmp_path / ".claude").mkdir()
    settings_path("global").write_text(json.dumps({"permissions": {"allow": ["Bash(ls:*)"]},
                                                   "hooks": {"PreToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "lint"}]}]}}))
    _, msg = install("global", wrap="noisy")
    s = json.loads(settings_path("global").read_text())
    assert "Installed" in msg
    assert s["permissions"]["allow"] == ["Bash(ls:*)", "Bash(/opt/skimread:*)"]
    assert len(s["hooks"]["PreToolUse"]) == 2
    assert s["hooks"]["PreToolUse"][1] == {"matcher": "Bash|Read", "hooks": [{"type": "command", "command": "/opt/skimread hook", "timeout": 30}]}
    install("global", wrap="off")  # idempotent re-install
    s = json.loads(settings_path("global").read_text())
    assert len(s["hooks"]["PreToolUse"]) == 2 and s["hooks"]["PreToolUse"][1]["matcher"] == "Read"
    assert s["permissions"]["allow"] == ["Bash(ls:*)"]
    install("global", uninstall=True)
    s = json.loads(settings_path("global").read_text())
    assert s["hooks"]["PreToolUse"] == [{"matcher": "Write", "hooks": [{"type": "command", "command": "lint"}]}]


def test_mcp_server_roundtrip(tmp_path):
    env = dict(os.environ, SKIMREAD_HOME=str(tmp_path / "h"))
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "run", "arguments": {"command": "echo hi; echo 'ERROR: x'"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "show", "arguments": {"id": 1, "grep": "ERROR"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "nope"},
    ]
    r = subprocess.run([sys.executable, "-m", "skimread", "mcp"], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                       capture_output=True, text=True, env=env)
    lines = [json.loads(l) for l in r.stdout.strip().split("\n")]
    assert lines[0]["result"]["serverInfo"]["name"] == "skimread"
    assert {t["name"] for t in lines[1]["result"]["tools"]} == {"run", "show", "outline", "read", "list"}
    assert "ERROR: x" in lines[2]["result"]["content"][0]["text"]
    assert "2| ERROR: x" in lines[3]["result"]["content"][0]["text"]
    assert lines[4]["error"]["code"] == -32601
