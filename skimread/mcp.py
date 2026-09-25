"""A dependency-free MCP server (stdio, newline-delimited JSON-RPC 2.0).

Exposes skimread to agents that have no hook system: run commands and get a
digest, retrieve full output slices, outline files, read line ranges.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List

from . import __version__, store
from .digest import build_digest, run_command
from .outline import outline_file

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "run",
        "description": "Run a shell command and return a short, lossless digest of its output (errors, "
                       "failures and summaries kept; noise folded). The digest ends with an id; use `show` "
                       "to fetch any part of the full output without re-running the command.",
        "inputSchema": {"type": "object", "properties": {
            "command": {"type": "string", "description": "Shell command line"},
            "cwd": {"type": "string", "description": "Working directory (optional)"},
            "timeout": {"type": "number", "description": "Seconds before the command is killed (optional)"},
            "budget": {"type": "integer", "description": "Digest size in characters (default from config, 6000)"},
        }, "required": ["command"]},
    },
    {
        "name": "show",
        "description": "Show the full output of a previous `run` by id, a line range (e.g. '120-180'), "
                       "or lines matching a regex with context.",
        "inputSchema": {"type": "object", "properties": {
            "id": {"type": "integer"},
            "range": {"type": "string", "description": "'A-B', 'A:', ':B' or 'A'"},
            "grep": {"type": "string", "description": "Regex to search for"},
            "context": {"type": "integer", "description": "Context lines around grep matches (default 2)"},
        }, "required": ["id"]},
    },
    {
        "name": "outline",
        "description": "Structural outline of a file: symbols/headings/keys with line ranges, so you can read "
                       "only the slice you need. Use before reading any file longer than ~300 lines.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "read",
        "description": "Read a line range of a file with line numbers ('start' and 'end' are 1-based, inclusive).",
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}},
            "required": ["path"]},
    },
    {
        "name": "list",
        "description": "List recent runs stored by skimread (id, command, exit code, size).",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
]


def _text(s: str, err: bool = False) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": s}], "isError": err}


def call_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    from .cli import format_show, read_range, format_list
    if name == "run":
        res = run_command(args["command"], cwd=args.get("cwd"), timeout=args.get("timeout"))
        d = build_digest(res["raw"], cmd=args["command"], exit_code=res["exit"], duration=res["duration"],
                         budget=args.get("budget"), cwd=args.get("cwd") or "")
        return _text(d.text, err=res["exit"] != 0)
    if name == "show":
        return _text(format_show(int(args["id"]), args.get("range"), args.get("grep"), int(args.get("context") or 2)))
    if name == "outline":
        return _text(outline_file(args["path"]))
    if name == "read":
        return _text(read_range(args["path"], args.get("start"), args.get("end")))
    if name == "list":
        return _text(format_list(int(args.get("limit") or 20)))
    return _text(f"unknown tool {name}", err=True)


def serve() -> int:
    out = sys.stdout
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except ValueError:
            continue
        rid = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        resp: Dict[str, Any]
        try:
            if method == "initialize":
                result: Any = {"protocolVersion": params.get("protocolVersion") or "2024-11-05",
                               "capabilities": {"tools": {}},
                               "serverInfo": {"name": "skimread", "version": __version__}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = call_tool(params.get("name", ""), params.get("arguments") or {})
            elif method and method.startswith("notifications/"):
                continue
            else:
                if rid is None:
                    continue
                resp = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}
                out.write(json.dumps(resp) + "\n")
                out.flush()
                continue
            if rid is None:
                continue
            resp = {"jsonrpc": "2.0", "id": rid, "result": result}
        except Exception as e:  # keep the server alive
            if rid is None:
                continue
            resp = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(e)}}
        out.write(json.dumps(resp) + "\n")
        out.flush()
    return 0
