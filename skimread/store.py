"""Local spool: full outputs, sequence ids, event log, session state, stats.

Everything lives under $SKIMREAD_HOME (default ~/.skimread). Files are created
with owner-only permissions because command output can contain secrets.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

_DEFAULT_CONFIG: Dict[str, Any] = {
    "budget": 6000,            # chars of digest per command (~1.7k tokens)
    "read_threshold_lines": 400,
    "read_threshold_chars": 16000,
    "wrap": "noisy",           # noisy | all | off  (Claude Code Bash hook)
    "keep_runs": 300,
    "keep_days": 7,
    "price_per_mtok": 3.0,     # USD per million input tokens, for `stats`
}


def home() -> Path:
    p = Path(os.environ.get("SKIMREAD_HOME") or Path.home() / ".skimread")
    for sub in ("", "out", "sessions"):
        d = p / sub if sub else p
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass
    return p


def load_config() -> Dict[str, Any]:
    cfg = dict(_DEFAULT_CONFIG)
    f = home() / "config.json"
    if f.exists():
        try:
            cfg.update(json.loads(f.read_text() or "{}"))
        except (OSError, ValueError):
            pass
    for key in cfg:
        env = os.environ.get("SKIMREAD_" + key.upper())
        if env is not None:
            try:
                cfg[key] = type(_DEFAULT_CONFIG[key])(env)
            except (TypeError, ValueError):
                cfg[key] = env
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    _write_private(home() / "config.json", json.dumps(cfg, indent=2) + "\n")


def _write_private(path: Path, text: str) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as f:
        f.write(text)


def next_id() -> int:
    seq = home() / "seq"
    fd = os.open(str(seq), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        raw = os.read(fd, 32).decode(errors="replace")
        try:
            n = int(raw.strip() or 0) + 1
        except ValueError:
            n = int(time.time())  # corrupt counter: jump to a unique-enough id
        os.lseek(fd, 0, 0)
        os.ftruncate(fd, 0)
        os.write(fd, str(n).encode())
        return n
    finally:
        os.close(fd)


def save_run(text: str, meta: Dict[str, Any]) -> int:
    rid = next_id()
    base = home() / "out"
    _write_private(base / f"{rid}.txt", text)
    meta = dict(meta, id=rid, ts=time.time())
    _write_private(base / f"{rid}.json", json.dumps(meta))
    if rid % 25 == 0:
        prune()
    return rid


def load_run(rid: int) -> Optional[Dict[str, Any]]:
    base = home() / "out"
    txt, js = base / f"{rid}.txt", base / f"{rid}.json"
    if not txt.exists():
        return None
    meta: Dict[str, Any] = {}
    if js.exists():
        try:
            meta = json.loads(js.read_text())
        except ValueError:
            meta = {}
    meta["text"] = txt.read_text(encoding="utf-8", errors="replace")
    return meta


def list_runs(limit: int = 20) -> List[Dict[str, Any]]:
    base = home() / "out"
    metas = []
    for js in base.glob("*.json"):
        try:
            metas.append(json.loads(js.read_text()))
        except (OSError, ValueError):
            continue
    metas.sort(key=lambda m: m.get("id", 0), reverse=True)
    return metas[:limit]


def prune() -> None:
    cfg = load_config()
    base = home() / "out"
    metas = list_runs(limit=100000)
    cutoff = time.time() - cfg["keep_days"] * 86400
    for i, m in enumerate(metas):
        if i >= cfg["keep_runs"] or m.get("ts", 0) < cutoff:
            for ext in (".txt", ".json"):
                try:
                    (base / f"{m['id']}{ext}").unlink()
                except OSError:
                    pass
    sess = home() / "sessions"
    for f in sess.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def log_event(ev: Dict[str, Any]) -> None:
    ev = {k: v for k, v in ev.items() if k != "text"}
    ev.setdefault("ts", time.time())
    path = home() / "events.jsonl"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev) + "\n")


def events() -> Iterator[Dict[str, Any]]:
    path = home() / "events.jsonl"
    if not path.exists():
        return
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                yield json.loads(line)
            except ValueError:
                continue


# --- per-session state (used by the Read guard) ---------------------------------

def session_state(session_id: str) -> Dict[str, Any]:
    f = home() / "sessions" / f"{_safe(session_id)}.json"
    if f.exists():
        try:
            return json.loads(f.read_text() or "{}")
        except (OSError, ValueError):
            pass
    return {}


def save_session_state(session_id: str, state: Dict[str, Any]) -> None:
    _write_private(home() / "sessions" / f"{_safe(session_id)}.json", json.dumps(state))


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (s or "default"))[:80]
