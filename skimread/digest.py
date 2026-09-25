"""Turn one command's raw output into a budgeted, lossless digest."""

from __future__ import annotations

import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import formats, store
from .ansi import normalize
from .reduce import Line, fit_budget, render, to_lines
from .tokens import estimate_tokens, fmt_bytes, fmt_tokens

MAX_CAPTURE = 32 * 1024 * 1024


@dataclass
class Digest:
    text: str
    run_id: Optional[int]
    fmt: str
    lines_in: int
    bytes_in: int
    tokens_in: int
    tokens_out: int


def build_digest(raw: str, cmd: str = "", exit_code: Optional[int] = None, duration: Optional[float] = None,
                 budget: Optional[int] = None, save: bool = True, cwd: str = "") -> Digest:
    cfg = store.load_config()
    budget = budget or int(cfg["budget"])
    norm = normalize(raw)
    lines: List[Line] = to_lines(norm)
    n_lines = len(lines)
    bytes_in = len(raw.encode("utf-8", errors="replace"))
    tokens_in = estimate_tokens(norm)

    run_id: Optional[int] = None
    if save:
        run_id = store.save_run(norm, {
            "cmd": cmd, "cwd": cwd, "exit": exit_code, "lines": n_lines, "bytes": bytes_in,
            "tokens_in": tokens_in, "duration": duration,
        })
    handle = f"skimread show {run_id}" if run_id else "skimread show <id>"

    fmt, reducer = formats.detect(cmd, lines)
    ctx = formats.Ctx(cmd=cmd, budget=budget, handle=handle)
    reduced = reducer(list(lines), ctx)
    fitted = fit_budget(reduced, budget, handle=handle)
    body = render(fitted)

    tokens_out = estimate_tokens(body)
    status = ""
    if exit_code is not None:
        status = f"exit {exit_code}"
        if duration is not None:
            status += f" in {duration:.1f}s"
    shown = len(fitted)
    header_bits = [f"$ {cmd}" if cmd else "$ (stdin)"]
    if status:
        header_bits.append(status)
    header = "  ·  ".join(header_bits)
    if n_lines <= shown and body == norm:
        footer = f"[skimread #{run_id}: {n_lines} lines, shown in full]" if run_id else ""
    else:
        footer = (f"[skimread #{run_id}: {n_lines} lines, ~{fmt_tokens(tokens_in)} tok → {fmt_tokens(tokens_out)}. "
                  f"Full/slice/search: {handle} [A-B | --grep RE]]") if run_id else f"[skimread: {n_lines} lines → digest]"
    text = header + "\n" + body + ("\n" + footer if footer else "")
    if run_id:
        try:
            store.log_event({"kind": "run", "id": run_id, "tokens_in": tokens_in, "lines": n_lines,
                             "tokens_out": estimate_tokens(text), "fmt": fmt, "cmd": cmd, "exit": exit_code})
        except OSError:
            pass
    return Digest(text, run_id, fmt, n_lines, bytes_in, tokens_in, estimate_tokens(text))


def run_command(cmd: str, cwd: Optional[str] = None, timeout: Optional[float] = None,
                shell: bool = True) -> Dict:
    """Run a command with merged stdout/stderr, colours off. Returns raw output + exit code."""
    import os
    env = dict(os.environ)
    env.update({"NO_COLOR": "1", "FORCE_COLOR": "0", "TERM": "dumb", "CLICOLOR": "0", "PY_COLORS": "0",
                "CARGO_TERM_COLOR": "never", "SKIMREAD_ACTIVE": "1"})
    env.pop("COLORTERM", None)
    start = time.time()
    try:
        if shell:
            bash = _bash()
            proc = subprocess.Popen(cmd if bash is None else [bash, "-c", cmd], shell=bash is None,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    cwd=cwd, env=env)
        else:
            proc = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, cwd=cwd, env=env)
    except FileNotFoundError as e:
        return {"raw": f"skimread: cannot run command: {e}\n", "exit": 127, "duration": 0.0, "timed_out": False}
    chunks: List[bytes] = []
    size = 0
    timed_out = False
    assert proc.stdout is not None
    try:
        while True:
            if timeout and time.time() - start > timeout:
                proc.kill()
                timed_out = True
                break
            chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_CAPTURE:
                # keep the tail; the head is almost never what an agent needs
                while size > MAX_CAPTURE // 2 and len(chunks) > 1:
                    size -= len(chunks.pop(0))
    finally:
        proc.wait()
    raw = b"".join(chunks).decode("utf-8", errors="replace")
    if timed_out:
        raw += f"\nskimread: command timed out after {timeout:.0f}s and was killed\n"
    return {"raw": raw, "exit": proc.returncode if not timed_out else 124,
            "duration": time.time() - start, "timed_out": timed_out}


def _bash() -> Optional[str]:
    import shutil
    if sys.platform.startswith("win"):
        return None
    return shutil.which("bash") or shutil.which("sh")
