"""skimread command line."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import __version__, store
from .digest import build_digest, run_command
from .outline import analyze, render_outline
from .tokens import estimate_tokens, fmt_bytes, fmt_tokens

SUBCOMMANDS = {"run", "pipe", "show", "list", "outline", "read", "stats", "hook", "setup", "mcp", "config",
               "prune", "version", "-h", "--help", "--version"}


# --- helpers shared with the MCP server ------------------------------------------------

def parse_range(spec: Optional[str], n: int) -> Optional[tuple]:
    if not spec:
        return None
    m = re.match(r"^\s*(\d*)\s*[-:]\s*(\d*)\s*$", spec)
    if m:
        a = int(m.group(1)) if m.group(1) else 1
        b = int(m.group(2)) if m.group(2) else n
        return max(1, a), min(n, b)
    if spec.strip().isdigit():
        a = int(spec)
        return max(1, a), min(n, a)
    raise SystemExit(f"skimread: bad range {spec!r} (use A-B, A:, :B or A)")


def _numbered(lines: List[str], start: int) -> str:
    w = len(str(start + len(lines)))
    return "\n".join(f"{start + i:>{w}}| {t}" for i, t in enumerate(lines))


def format_show(rid: int, rng: Optional[str] = None, grep: Optional[str] = None, context: int = 2,
                show_all: bool = False, cap: int = 400) -> str:
    run = store.load_run(rid)
    if run is None:
        return f"skimread: no stored output with id {rid} (see `skimread list`)"
    lines = run["text"].split("\n")
    n = len(lines)
    head = f"# skimread #{rid}: $ {run.get('cmd', '')}  ·  exit {run.get('exit')}  ·  {n} lines"
    if grep:
        try:
            rx = re.compile(grep, re.I)
        except re.error as e:
            return f"skimread: bad regex: {e}"
        hits = [i for i, t in enumerate(lines) if rx.search(t)]
        if not hits:
            return head + f"\n(no lines match /{grep}/)"
        keep = set()
        for i in hits:
            keep.update(range(max(0, i - context), min(n, i + context + 1)))
        out = [head + f"  ·  {len(hits)} matches for /{grep}/"]
        prev = -1
        shown = 0
        for i in sorted(keep):
            if prev >= 0 and i != prev + 1:
                out.append("   …")
            out.append(f"{i + 1:>5}| {lines[i]}")
            prev = i
            shown += 1
            if shown >= cap * 2 and not show_all:
                out.append(f"   … stopped after {shown} lines; narrow the pattern or pass --all")
                break
        return "\n".join(out)
    r = parse_range(rng, n)
    if r:
        a, b = r
        sel = lines[a - 1:b]
        note = ""
        if len(sel) > cap * 4 and not show_all:
            sel = sel[: cap * 4]
            note = f"\n   … range truncated to {cap * 4} lines; pass --all to print everything"
        return head + "\n" + _numbered(sel, a) + note
    if n > cap and not show_all:
        return (head + f"\n" + _numbered(lines[:cap // 2], 1) +
                f"\n   … {n - cap} lines omitted ({cap // 2 + 1}-{n - cap // 2}); use a range, --grep, or --all …\n" +
                _numbered(lines[n - cap // 2:], n - cap // 2 + 1))
    return head + "\n" + _numbered(lines, 1)


def read_range(path: str, start: Optional[int] = None, end: Optional[int] = None) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"skimread: {e}"
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    n = len(lines)
    a = max(1, int(start or 1))
    b = min(n, int(end or n))
    if a > b:
        return f"skimread: empty range {a}-{b} (file has {n} lines)"
    return f"# {path} lines {a}-{b} of {n}\n" + _numbered(lines[a - 1:b], a)


def format_list(limit: int = 20) -> str:
    runs = store.list_runs(limit)
    if not runs:
        return "skimread: no stored runs yet"
    out = ["   id  exit   lines   tokens  when              command"]
    for m in runs:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(m.get("ts", 0)))
        cmd = (m.get("cmd") or "")[:70]
        out.append(f"{m.get('id', 0):>5}  {str(m.get('exit')):>4}  {m.get('lines', 0):>6}  {fmt_tokens(m.get('tokens_in', 0)):>7}  {when}  {cmd}")
    return "\n".join(out)


def format_stats(price: Optional[float] = None, as_json: bool = False) -> str:
    cfg = store.load_config()
    price = price if price is not None else float(cfg["price_per_mtok"])
    runs = 0
    run_in = run_out = 0
    guards = 0
    guard_full = guard_outline = slices = full_reads = 0
    by_fmt: dict = {}
    for ev in store.events():
        k = ev.get("kind")
        if k == "run":
            runs += 1
            run_in += int(ev.get("tokens_in", 0))
            run_out += int(ev.get("tokens_out", 0))
            f = ev.get("fmt", "generic")
            d = by_fmt.setdefault(f, [0, 0, 0])
            d[0] += 1
            d[1] += int(ev.get("tokens_in", 0))
            d[2] += int(ev.get("tokens_out", 0))
        elif k == "read_guard":
            guards += 1
            guard_full += int(ev.get("tokens", 0))
            guard_outline += int(ev.get("outline_tokens", 0))
        elif k == "read_slice":
            slices += int(ev.get("tokens", 0))
        elif k == "read_full":
            full_reads += int(ev.get("tokens", 0))
    run_saved = max(0, run_in - run_out)
    read_spent = guard_outline + slices + full_reads
    read_saved = max(0, guard_full - read_spent)
    total = run_saved + read_saved
    usd = total / 1e6 * price
    if as_json:
        return json.dumps({"runs": runs, "run_tokens_in": run_in, "run_tokens_out": run_out, "run_saved": run_saved,
                           "read_guards": guards, "read_tokens_full": guard_full, "read_tokens_spent": read_spent,
                           "read_saved": read_saved, "total_saved": total, "usd_saved": round(usd, 4),
                           "price_per_mtok": price, "by_format": by_fmt}, indent=2)
    lines = [
        "skimread stats (all time)",
        f"  commands digested : {runs:>7}   {fmt_tokens(run_in):>7} tok → {fmt_tokens(run_out):>7} tok   saved {fmt_tokens(run_saved)}"
        + (f" ({100 * run_saved / run_in:.0f}%)" if run_in else ""),
        f"  big reads guarded : {guards:>7}   {fmt_tokens(guard_full):>7} tok → {fmt_tokens(read_spent):>7} tok   saved {fmt_tokens(read_saved)}"
        + (f" ({100 * read_saved / guard_full:.0f}%)" if guard_full else ""),
        f"  total saved       : ~{fmt_tokens(total)} tokens ≈ ${usd:.2f} at ${price:g}/M input tokens",
    ]
    if by_fmt:
        lines.append("  by format:")
        for f, (c, i, o) in sorted(by_fmt.items(), key=lambda kv: -kv[1][1]):
            pct = f"{100 * (i - o) / i:.0f}%" if i else "-"
            lines.append(f"    {f:<10} {c:>5} runs  {fmt_tokens(i):>7} → {fmt_tokens(o):>7}  ({pct})")
    return "\n".join(lines)


# --- subcommands ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    if args.c:
        cmd = args.c
    elif args.argv:
        argv = args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv
        import shlex
        cmd = " ".join(shlex.quote(a) for a in argv)
    else:
        print("skimread run: give a command (skimread run -c 'pytest -q' or skimread run -- pytest -q)", file=sys.stderr)
        return 2
    res = run_command(cmd, cwd=args.cwd, timeout=args.timeout)
    d = build_digest(res["raw"], cmd=cmd, exit_code=res["exit"], duration=res["duration"], budget=args.budget,
                     cwd=args.cwd or "")
    sys.stdout.write(d.text + "\n")
    return int(res["exit"] or 0)


def cmd_pipe(args: argparse.Namespace) -> int:
    raw = sys.stdin.read()
    exit_code = None
    m = re.search(r"\n?__SKIMREAD_EXIT=(\d+)\s*$", raw)
    if m:
        exit_code = int(m.group(1))
        raw = raw[: m.start()]
    d = build_digest(raw, cmd=args.cmd or "", exit_code=exit_code, budget=args.budget)
    sys.stdout.write(d.text + "\n")
    return exit_code or 0


def cmd_show(args: argparse.Namespace) -> int:
    print(format_show(args.id, args.range, args.grep, args.context, args.all))
    return 0


def cmd_outline(args: argparse.Namespace) -> int:
    for p in args.paths:
        if not Path(p).is_file():
            print(f"skimread: {p}: not a file", file=sys.stderr)
            continue
        print(render_outline(analyze(p), max_entries=100000 if args.all else args.max))
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    r = parse_range(args.range, 10 ** 9) if args.range else None
    print(read_range(args.path, r[0] if r else None, r[1] if r else None))
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    from .hooks import handle
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    try:
        out = handle(payload)
    except Exception as e:  # never break the agent's tool call
        print(f"skimread hook error: {e}", file=sys.stderr)
        return 0
    if out:
        sys.stdout.write(json.dumps(out))
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    from . import setup as st
    if args.target == "claude":
        scope = "project" if args.project else ("local" if args.local else "global")
        _, msg = st.install(scope=scope, wrap=args.wrap, uninstall=args.uninstall)
        print(msg)
    elif args.target == "print":
        print(st.snippet(args.wrap))
    elif args.target == "agents":
        print(st.agents_snippet())
    elif args.target == "mcp":
        print(st.mcp_snippet())
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = store.load_config()
    if args.key and args.value is not None:
        if args.key not in cfg:
            print(f"skimread: unknown key {args.key}; keys: {', '.join(cfg)}", file=sys.stderr)
            return 2
        cur = cfg[args.key]
        try:
            cfg[args.key] = type(cur)(args.value) if not isinstance(cur, bool) else args.value.lower() in ("1", "true", "yes")
        except ValueError:
            print(f"skimread: {args.key} expects {type(cur).__name__}", file=sys.stderr)
            return 2
        store.save_config(cfg)
    if args.key:
        print(f"{args.key} = {cfg[args.key]}")
    else:
        for k, v in cfg.items():
            print(f"{k} = {v}")
        print(f"(config file: {store.home() / 'config.json'})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="skimread", description="Skim before you read: lossless context guard for AI coding agents.")
    p.add_argument("--version", action="version", version=f"skimread {__version__}")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="run a command and print a lossless digest of its output")
    r.add_argument("-c", help="command line to run through the shell")
    r.add_argument("--cwd")
    r.add_argument("--timeout", type=float)
    r.add_argument("--budget", type=int, help="digest size in characters")
    r.add_argument("argv", nargs=argparse.REMAINDER)
    r.set_defaults(fn=cmd_run)

    pi = sub.add_parser("pipe", help="digest stdin (cmd 2>&1 | skimread pipe)")
    pi.add_argument("--cmd", help="command name, for format detection and the header")
    pi.add_argument("--budget", type=int)
    pi.set_defaults(fn=cmd_pipe)

    s = sub.add_parser("show", help="show stored full output: all, a range, or grep matches")
    s.add_argument("id", type=int)
    s.add_argument("range", nargs="?")
    s.add_argument("--grep", "-g")
    s.add_argument("-C", "--context", type=int, default=2)
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_show)

    l = sub.add_parser("list", help="list stored runs")
    l.add_argument("-n", type=int, default=20)
    l.set_defaults(fn=lambda a: (print(format_list(a.n)), 0)[1])

    o = sub.add_parser("outline", help="structural outline of a file with line ranges")
    o.add_argument("paths", nargs="+")
    o.add_argument("--max", type=int, default=70)
    o.add_argument("--all", action="store_true")
    o.set_defaults(fn=cmd_outline)

    rd = sub.add_parser("read", help="print a line range of a file with line numbers")
    rd.add_argument("path")
    rd.add_argument("range", nargs="?")
    rd.set_defaults(fn=cmd_read)

    stt = sub.add_parser("stats", help="tokens and money saved so far")
    stt.add_argument("--price", type=float, help="USD per million input tokens")
    stt.add_argument("--json", action="store_true")
    stt.set_defaults(fn=lambda a: (print(format_stats(a.price, a.json)), 0)[1])

    h = sub.add_parser("hook", help="Claude Code PreToolUse hook (JSON on stdin)")
    h.set_defaults(fn=cmd_hook)

    se = sub.add_parser("setup", help="install into Claude Code, or print snippets")
    se.add_argument("target", choices=["claude", "print", "agents", "mcp"])
    se.add_argument("--project", action="store_true", help="write .claude/settings.json in the current project")
    se.add_argument("--local", action="store_true", help="write .claude/settings.local.json (not committed)")
    se.add_argument("--wrap", choices=["noisy", "all", "off"], default="noisy",
                    help="which Bash commands to route through skimread (default: noisy)")
    se.add_argument("--uninstall", action="store_true")
    se.set_defaults(fn=cmd_setup)

    m = sub.add_parser("mcp", help="run as an MCP server on stdio")
    m.set_defaults(fn=lambda a: __import__("skimread.mcp", fromlist=["serve"]).serve())

    c = sub.add_parser("config", help="show or set configuration")
    c.add_argument("key", nargs="?")
    c.add_argument("value", nargs="?")
    c.set_defaults(fn=cmd_config)

    pr = sub.add_parser("prune", help="delete old stored outputs")
    pr.set_defaults(fn=lambda a: (store.prune(), print("pruned"), 0)[2])

    v = sub.add_parser("version")
    v.set_defaults(fn=lambda a: (print(f"skimread {__version__}"), 0)[1])
    return p


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in SUBCOMMANDS and not argv[0].startswith("-"):
        argv = ["run", "--"] + argv  # `skimread pytest -q` == `skimread run -- pytest -q`
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0
    try:
        return int(args.fn(args) or 0)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
