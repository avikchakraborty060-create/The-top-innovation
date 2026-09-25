"""Structural outlines of files: the "skim" in skimread.

Given a path, produce a compact map of where things are (symbols with line
ranges for code, headings for Markdown, keys for JSON/YAML, shape for CSV and
logs) so an agent can read only the slice it needs. No parser dependencies:
Python uses the stdlib ast module, brace languages use regexes plus brace
matching, indentation languages use indentation. Good enough to navigate;
the file itself remains the source of truth.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from .tokens import estimate_tokens, fmt_tokens


@dataclass
class Entry:
    start: int
    end: int
    kind: str
    name: str
    depth: int = 0
    extra: str = ""


@dataclass
class Outline:
    path: str
    lang: str
    n_lines: int
    n_chars: int
    entries: List[Entry] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".tgz",
              ".bz2", ".xz", ".7z", ".jar", ".war", ".so", ".dylib", ".dll", ".exe", ".bin", ".wasm", ".pyc",
              ".class", ".o", ".a", ".mp3", ".mp4", ".mov", ".avi", ".woff", ".woff2", ".ttf", ".otf", ".ipynb",
              ".sqlite", ".db", ".parquet", ".npy", ".npz", ".pkl", ".lock"}

_LANG_BY_EXT = {
    ".py": "python", ".pyi": "python",
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".ts": "js", ".tsx": "js", ".mts": "js", ".cts": "js",
    ".vue": "js", ".svelte": "js",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "java", ".kts": "java", ".scala": "java", ".cs": "java",
    ".swift": "java", ".dart": "java", ".php": "php",
    ".c": "c", ".h": "c", ".cc": "c", ".cpp": "c", ".cxx": "c", ".hpp": "c", ".hh": "c", ".m": "c", ".mm": "c",
    ".rb": "ruby", ".ex": "ruby", ".exs": "ruby", ".lua": "ruby", ".el": "lisp", ".clj": "lisp",
    ".md": "markdown", ".mdx": "markdown", ".rst": "markdown", ".txt": "text",
    ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".ini": "toml", ".cfg": "toml", ".env": "toml", ".properties": "toml",
    ".csv": "csv", ".tsv": "csv", ".log": "log", ".sql": "sql", ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".html": "html", ".htm": "html", ".xml": "html", ".svg": "html", ".css": "css", ".scss": "css", ".less": "css",
    ".tf": "yaml", ".proto": "c", ".graphql": "c", ".gql": "c",
}


def is_binary_path(path: str) -> bool:
    return Path(path).suffix.lower() in BINARY_EXT


def detect_lang(path: str, text: str) -> str:
    ext = Path(path).suffix.lower()
    name = Path(path).name.lower()
    if name in ("dockerfile", "makefile", "cmakelists.txt"):
        return "shell"
    if ext in _LANG_BY_EXT:
        return _LANG_BY_EXT[ext]
    head = text[:2000]
    if head.startswith("#!"):
        first = head.split("\n", 1)[0]
        if "python" in first:
            return "python"
        if "node" in first or "deno" in first or "bun" in first:
            return "js"
        return "shell"
    if re.search(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", head, re.M) or re.search(r"\b(?:INFO|ERROR|WARN(?:ING)?|DEBUG)\b", head):
        return "log"
    return "text"


# --- python ------------------------------------------------------------------------

def _py_sig(node: ast.AST) -> str:
    try:
        args = ast.unparse(node.args)  # type: ignore[attr-defined]
    except Exception:
        args = "..."
    ret = ""
    if getattr(node, "returns", None) is not None:
        try:
            ret = " -> " + ast.unparse(node.returns)  # type: ignore[attr-defined]
        except Exception:
            ret = ""
    return f"({args}){ret}"


def outline_python(text: str, out: Outline) -> None:
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        out.notes.append(f"syntax error at line {e.lineno}; falling back to indentation scan")
        outline_indent(text, out, re.compile(r"^(\s*)(?:async\s+)?(def|class)\s+(\w+)"))
        return
    imports: List[Tuple[int, int]] = []

    def walk(body, depth: int):
        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.append((node.lineno, getattr(node, "end_lineno", node.lineno)))
                continue
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                deco = " @" + ",".join(_deco(d) for d in node.decorator_list) if node.decorator_list else ""
                out.entries.append(Entry(node.lineno, node.end_lineno or node.lineno, kind, node.name + _py_sig(node), depth, deco))
                walk(node.body, depth + 1)
            elif isinstance(node, ast.ClassDef):
                bases = ", ".join(_deco(b) for b in node.bases)
                out.entries.append(Entry(node.lineno, node.end_lineno or node.lineno, "class",
                                         node.name + (f"({bases})" if bases else ""), depth))
                walk(node.body, depth + 1)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and depth == 0:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = [t.id for t in targets if isinstance(t, ast.Name)]
                if names and (node.end_lineno or node.lineno) - node.lineno >= 3 or (names and names[0].isupper()):
                    out.entries.append(Entry(node.lineno, node.end_lineno or node.lineno, "var", ", ".join(names), depth))
            elif isinstance(node, ast.If) and depth == 0 and "__main__" in ast.dump(node.test):
                out.entries.append(Entry(node.lineno, node.end_lineno or node.lineno, "if", "__name__ == '__main__'", depth))

    walk(tree.body, 0)
    if imports:
        out.entries.append(Entry(imports[0][0], imports[-1][1], "imports", f"{len(imports)} import statements", 0))
    out.entries.sort(key=lambda e: (e.start, e.depth))


def _deco(node: ast.AST) -> str:
    try:
        return ast.unparse(node)  # type: ignore[attr-defined]
    except Exception:
        return "?"


# --- brace languages --------------------------------------------------------------------

_BRACE_PATTERNS = {
    "js": [
        (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*(\([^)]*\))?"), "function", 0),
        (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+[\w.]+)?"), "class", 0),
        (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*(?::\s*[^=]+)?=>"), "fn", 0),
        (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::[^=]+)?=\s*(?:async\s+)?function"), "fn", 0),
        (re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?(?:const\s+)?(interface|type|enum|namespace|module)\s+(\w+)"), "type", 0),
        (re.compile(r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::[^=]+)?=\s*[\[{]"), "const", 0),
        (re.compile(r"^(?:export\s+default\s+|module\.exports\s*=\s*)(\{|\w+)"), "export", 0),
        (re.compile(r"^(describe|context|suite)\s*\(\s*['\"`]([^'\"`]+)"), "describe", 0),
        (re.compile(r"^\s+(?:(?:public|private|protected|static|async|readonly|override|abstract|get|set)\s+)*\*?(\w+)\s*(?:<[^>]*>)?\s*\([^)]*\)\s*(?::\s*[^{;=]+)?\s*\{"), "method", 1),
        (re.compile(r"^\s+(?:(?:public|private|protected|static|readonly)\s+)*(\w+)\s*(?::[^=]+)?=\s*(?:async\s*)?\([^)]*\)\s*(?::[^=]+)?=>"), "method", 1),
        (re.compile(r"^\s+(describe|it|test)\s*\(\s*['\"`]([^'\"`]+)"), "test", 1),
    ],
    "go": [
        (re.compile(r"^func\s+(?:\(\s*\w*\s*\*?(\w+)\s*\)\s*)?(\w+)\s*(\([^)]*\))"), "func", 0),
        (re.compile(r"^type\s+(\w+)\s+(struct|interface)"), "type", 0),
        (re.compile(r"^type\s+(\w+)\s"), "type", 0),
        (re.compile(r"^(var|const|import)\s*\("), "block", 0),
    ],
    "rust": [
        (re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?fn\s+(\w+)"), "fn", 0),
        (re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(struct|enum|trait|union|type)\s+(\w+)"), "type", 0),
        (re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?impl(?:<[^>]*>)?\s+(.+?)\s*(?:\{|where|$)"), "impl", 0),
        (re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)"), "mod", 0),
        (re.compile(r"^\s*#\[(?:cfg\()?test"), "tests", 0),
        (re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const|static)\s+(\w+)"), "const", 0),
    ],
    "java": [
        (re.compile(r"^\s*(?:(?:public|private|protected|static|final|abstract|sealed|data|open|internal|export)\s+)*(?:class|interface|enum|record|object|struct|protocol|extension|trait|actor)\s+(\w+)"), "class", 0),
        (re.compile(r"^\s*(?:(?:public|private|protected|static|final|abstract|synchronized|override|open|suspend|async|internal|virtual|extern)\s+)*(?:fun|func|def)\s+(\w+)"), "method", 1),
        (re.compile(r"^\s+(?:(?:public|private|protected|static|final|abstract|synchronized|override|virtual|async|extern|unsafe)\s+)+[\w<>\[\],.?]+\s+(\w+)\s*\([^)]*\)\s*(?:throws\s+[\w.,\s]+)?\s*\{"), "method", 1),
        (re.compile(r"^\s+[\w<>\[\],.?]+\s+(\w+)\s*\([^)]*\)\s*(?:throws\s+[\w.,\s]+)?\s*\{"), "method", 1),
    ],
    "c": [
        (re.compile(r"^(?:typedef\s+)?(struct|class|enum|union|namespace)\s+(\w+)"), "type", 0),
        (re.compile(r"^[\w:<>*&~\s]+?[\s*&]+(\w+(?:::\w+)*)\s*\([^;]*\)\s*(?:const|override|noexcept|final|\s)*\{?\s*$"), "function", 0),
        (re.compile(r"^(\w+(?:::\w+)*)\s*\([^;]*\)\s*(?:const|override|noexcept|final|\s)*\{?\s*$"), "function", 0),
        (re.compile(r"^#define\s+(\w+)"), "define", 0),
        (re.compile(r"^(?:message|service|enum)\s+(\w+)"), "type", 0),
    ],
    "php": [
        (re.compile(r"^\s*(?:(?:public|private|protected|static|abstract|final)\s+)*function\s+(\w+)"), "function", 1),
        (re.compile(r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait|enum)\s+(\w+)"), "class", 0),
    ],
    "css": [
        (re.compile(r"^(@media[^{]*|@keyframes\s+\S+|[.#]?[\w-]+(?:[\s,>+~][^{]*)?)\s*\{"), "rule", 0),
    ],
}


def _brace_end(lines: List[str], start_idx: int, max_scan: int = 20000) -> int:
    first = lines[start_idx]
    if "{" not in first and first.rstrip().endswith(";"):
        return start_idx + 1
    depth = 0
    seen = False
    in_block_comment = False
    for i in range(start_idx, min(len(lines), start_idx + max_scan)):
        line = lines[i]
        j = 0
        quote = None
        while j < len(line):
            ch = line[j]
            if in_block_comment:
                if line.startswith("*/", j):
                    in_block_comment = False
                    j += 2
                    continue
                j += 1
                continue
            if quote:
                if ch == "\\":
                    j += 2
                    continue
                if ch == quote:
                    quote = None
                j += 1
                continue
            if line.startswith("//", j) or (ch == "#" and lines and not line.lstrip().startswith("#[") and False):
                break
            if line.startswith("/*", j):
                in_block_comment = True
                j += 2
                continue
            if ch in "\"'`":
                quote = ch
            elif ch == "{":
                depth += 1
                seen = True
            elif ch == "}":
                depth -= 1
                if seen and depth <= 0:
                    return i + 1
            j += 1
        if not seen and i > start_idx + 3:
            return start_idx + 1  # a declaration without a body (prototype, type alias)
    return min(len(lines), start_idx + 1) if not seen else out.n_lines


_KEYWORDS = {"struct", "interface", "enum", "type", "trait", "union", "namespace", "module", "class", "record",
             "object", "message", "service", "protocol", "extension", "actor"}


def outline_brace(text: str, out: Outline, lang: str) -> None:
    lines = text.split("\n")
    pats = _BRACE_PATTERNS[lang]
    stack: List[Tuple[int, int]] = []  # (end_line, depth) of enclosing containers
    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith(("//", "*", "/*", "#")) and lang != "c":
            continue
        for rx, kind, base_depth in pats:
            m = rx.match(line)
            if not m:
                continue
            groups = [g for g in m.groups() if g]
            if not groups:
                continue
            name = groups[0]
            if kind in ("type", "class", "describe", "test") and len(groups) > 1:
                kw = [g for g in groups if g in _KEYWORDS]
                others = [g for g in groups if g not in _KEYWORDS]
                if kw and others:
                    kind, name = kw[0], others[0]
                else:
                    name = groups[-1]
            if kind == "func":
                if lang == "go":
                    m2 = re.match(r"^func\s+(?:\(\s*\w*\s*\*?(\w+)\s*\)\s*)?(\w+)\s*(\([^)]*\))", line)
                    if m2:
                        recv, fname, params = m2.groups()
                        name = (f"({recv}).{fname}" if recv else fname) + params
                elif len(groups) > 1:
                    name = groups[0] + groups[1]
            if kind == "function" and len(groups) > 1 and groups[1].startswith("("):
                name = groups[0] + groups[1]
            if kind in ("function", "fn") and lang == "c" and name in ("if", "for", "while", "switch", "return", "else", "sizeof", "catch"):
                continue
            if lang == "js" and kind == "method" and name in ("if", "for", "while", "switch", "catch", "function", "return", "constructor") and name != "constructor":
                continue
            end = _brace_end(lines, i)
            while stack and stack[-1][0] < i + 1:
                stack.pop()
            depth = len(stack)
            if kind == "tests":
                name = "#[cfg(test)]"
            out.entries.append(Entry(i + 1, end, kind, name.strip(), depth))
            if end > i + 1:
                stack.append((end, depth))
            break
    # imports block
    imp = [] if lang == "go" else [i + 1 for i, l in enumerate(lines[:400]) if re.match(r"^\s*(?:import\s|from\s.*import|use\s|#include|require\(|const .* = require\()", l)]
    if imp:
        out.entries.append(Entry(imp[0], imp[-1], "imports", f"{len(imp)} import lines", 0))
    out.entries.sort(key=lambda e: (e.start, e.depth))


# --- indentation languages ---------------------------------------------------------------------

def outline_indent(text: str, out: Outline, rx: "re.Pattern[str]") -> None:
    lines = text.split("\n")
    n = len(lines)
    for i, line in enumerate(lines):
        m = rx.match(line)
        if not m:
            continue
        indent = len(m.group(1).expandtabs(4))
        j = i + 1
        last = i
        while j < n:
            l = lines[j]
            if l.strip():
                ind = len(l[: len(l) - len(l.lstrip())].expandtabs(4))
                if ind <= indent and not l.strip().startswith(("end", "}", "else", "elsif", "rescue", "ensure")):
                    break
                if ind <= indent and l.strip().startswith(("end", "}")):
                    last = j
                    break
                last = j
            j += 1
        out.entries.append(Entry(i + 1, last + 1, m.group(2), m.group(3), indent // 2 if indent else 0))


# --- data / text formats ----------------------------------------------------------------------

def outline_markdown(text: str, out: Outline) -> None:
    lines = text.split("\n")
    heads = [(i + 1, len(m.group(1)), m.group(2).strip()) for i, l in enumerate(lines)
             if (m := re.match(r"^(#{1,6})\s+(.*)", l))]
    in_code = False
    cleaned = []
    for i, l in enumerate(lines):
        if l.startswith("```"):
            in_code = not in_code
        if not in_code:
            m = re.match(r"^(#{1,6})\s+(.*)", l)
            if m:
                cleaned.append((i + 1, len(m.group(1)), m.group(2).strip()))
    heads = cleaned or heads
    for k, (ln, lvl, title) in enumerate(heads):
        end = heads[k + 1][0] - 1 if k + 1 < len(heads) else out.n_lines
        out.entries.append(Entry(ln, end, "h" + str(lvl), title, lvl - 1))


def outline_json(text: str, out: Outline) -> None:
    lines = text.split("\n")
    if len(lines) < 3:
        try:
            data = json.loads(text)
        except ValueError:
            out.notes.append("single-line JSON (could not parse)")
            return
        if isinstance(data, dict):
            keys = list(data.keys())
            out.notes.append(f"single-line JSON object with {len(keys)} keys: " + ", ".join(keys[:30]) + (" …" if len(keys) > 30 else ""))
        elif isinstance(data, list):
            out.notes.append(f"single-line JSON array with {len(data)} items; item type {type(data[0]).__name__ if data else 'n/a'}")
        return
    # pretty printed: depth-1 keys are indented by the first indent unit
    first_indent = None
    for l in lines[1:50]:
        if l.strip():
            first_indent = len(l) - len(l.lstrip())
            break
    if not first_indent:
        return
    keyrx = re.compile(r'^\s{%d}"([^"]+)"\s*:' % first_indent)
    itemrx = re.compile(r"^\s{%d}[\[{]" % first_indent)
    keys = [(i + 1, m.group(1)) for i, l in enumerate(lines) if (m := keyrx.match(l))]
    for k, (ln, key) in enumerate(keys):
        end = keys[k + 1][0] - 1 if k + 1 < len(keys) else out.n_lines - 1
        out.entries.append(Entry(ln, end, "key", key, 0))
    if not keys:
        items = sum(1 for l in lines if itemrx.match(l))
        if items:
            out.notes.append(f"top-level array with ~{items} items")


def outline_yaml(text: str, out: Outline) -> None:
    lines = text.split("\n")
    keys = [(i + 1, m.group(1)) for i, l in enumerate(lines) if (m := re.match(r"^([\w.\-\"'/\[\]]+)\s*[:=]", l))]
    for k, (ln, key) in enumerate(keys):
        end = keys[k + 1][0] - 1 if k + 1 < len(keys) else out.n_lines
        if end - ln >= 0:
            out.entries.append(Entry(ln, end, "key", key, 0))
    sections = [(i + 1, m.group(1)) for i, l in enumerate(lines) if (m := re.match(r"^\[([^\]]+)\]\s*$", l))]
    for k, (ln, sec) in enumerate(sections):
        end = sections[k + 1][0] - 1 if k + 1 < len(sections) else out.n_lines
        out.entries.append(Entry(ln, end, "section", f"[{sec}]", 0))
    out.entries.sort(key=lambda e: e.start)


def outline_csv(text: str, out: Outline) -> None:
    lines = text.split("\n")
    if not lines:
        return
    delim = "\t" if "\t" in lines[0] else ","
    cols = [c.strip().strip('"') for c in lines[0].split(delim)]
    out.notes.append(f"{len(cols)} columns: " + ", ".join(cols[:25]) + (" …" if len(cols) > 25 else ""))
    out.notes.append(f"{max(0, out.n_lines - 1)} data rows (header on line 1); sample row 2: {lines[1][:200] if len(lines) > 1 else ''}")


def outline_log(text: str, out: Outline) -> None:
    lines = text.split("\n")
    levels = {"ERROR": [], "FATAL": [], "WARN": [], "INFO": 0, "DEBUG": 0}
    stamp = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}|\d{2}:\d{2}:\d{2}")
    first = last = None
    for i, l in enumerate(lines):
        m = stamp.search(l)
        if m:
            last = m.group(0)
            first = first or last
        u = l.upper()
        if "ERROR" in u or "EXCEPTION" in u or "TRACEBACK" in u or "PANIC" in u:
            levels["ERROR"].append(i + 1)
        elif "FATAL" in u or "CRITICAL" in u:
            levels["FATAL"].append(i + 1)
        elif "WARN" in u:
            levels["WARN"].append(i + 1)
        elif "INFO" in u:
            levels["INFO"] += 1
        elif "DEBUG" in u or "TRACE" in u:
            levels["DEBUG"] += 1
    if first:
        out.notes.append(f"timestamps {first} → {last}")
    for lvl in ("FATAL", "ERROR", "WARN"):
        lst = levels[lvl]
        if lst:
            sample = ", ".join(str(x) for x in lst[:8]) + (" …" if len(lst) > 8 else "")
            out.notes.append(f"{len(lst)} {lvl} lines at: {sample}")
    out.notes.append(f"{levels['INFO']} INFO, {levels['DEBUG']} DEBUG/TRACE lines")


def outline_text(text: str, out: Outline) -> None:
    lines = text.split("\n")
    secs = [(i + 1, l.strip()) for i, l in enumerate(lines)
            if l.strip() and (re.match(r"^(?:[A-Z][A-Z0-9 _\-]{3,}|=+|-{3,}|#+ .*|\d+\.\s+\S.*|[A-Z].*:)\s*$", l.strip()))]
    for k, (ln, title) in enumerate(secs[:80]):
        end = secs[k + 1][0] - 1 if k + 1 < len(secs) else out.n_lines
        out.entries.append(Entry(ln, end, "section", title[:80], 0))


def outline_shell(text: str, out: Outline) -> None:
    rx = re.compile(r"^(?:function\s+)?([\w\-.]+)\s*\(\)\s*\{|^([\w\-./%$()]+)\s*:(?:\s|$)|^(FROM|RUN|COPY|ENTRYPOINT|CMD|WORKDIR|ARG|ENV)\b")
    lines = text.split("\n")
    for i, l in enumerate(lines):
        m = rx.match(l)
        if not m:
            continue
        name = m.group(1) or m.group(2) or l.strip()[:60]
        kind = "function" if m.group(1) else ("target" if m.group(2) else "step")
        end = _brace_end(lines, i) if m.group(1) else i + 1
        if kind == "target":
            j = i + 1
            while j < len(lines) and (lines[j].startswith("\t") or not lines[j].strip()):
                j += 1
            end = j
        out.entries.append(Entry(i + 1, end, kind, name, 0))


def outline_sql(text: str, out: Outline) -> None:
    rx = re.compile(r"^\s*(CREATE(?:\s+OR\s+REPLACE)?\s+(?:TABLE|VIEW|FUNCTION|PROCEDURE|INDEX|TRIGGER|TYPE)|ALTER\s+TABLE|INSERT\s+INTO|WITH|SELECT|UPDATE|DELETE\s+FROM|DROP)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?([\w.\"]+)?", re.I)
    lines = text.split("\n")
    marks = [(i + 1, (m.group(1) + " " + (m.group(2) or "")).strip()) for i, l in enumerate(lines) if (m := rx.match(l))]
    for k, (ln, name) in enumerate(marks):
        end = marks[k + 1][0] - 1 if k + 1 < len(marks) else out.n_lines
        out.entries.append(Entry(ln, end, "stmt", name[:80], 0))


def outline_html(text: str, out: Outline) -> None:
    rx = re.compile(r"<(script|style|template|section|article|main|nav|header|footer|form|table|svg|h[1-6]|div\s+id=\"[^\"]+\")[\s>]", re.I)
    lines = text.split("\n")
    for i, l in enumerate(lines):
        m = rx.search(l)
        if m:
            out.entries.append(Entry(i + 1, i + 1, "tag", m.group(1)[:60], 0))
    out.entries = out.entries[:120]


# --- entry point -----------------------------------------------------------------------------

def analyze(path: str, text: Optional[str] = None, lang: Optional[str] = None) -> Outline:
    if text is None:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    lang = lang or detect_lang(path, text)
    lines = text.split("\n")
    out = Outline(path, lang, len(lines) - (1 if text.endswith("\n") else 0), len(text))
    if lang == "python":
        outline_python(text, out)
    elif lang in _BRACE_PATTERNS:
        outline_brace(text, out, lang)
    elif lang == "ruby":
        outline_indent(text, out, re.compile(r"^(\s*)(def|class|module|defmodule|defp?|function|local function|describe|it|context)\s+([\w.?!:\"']+)"))
    elif lang == "lisp":
        outline_indent(text, out, re.compile(r"^(\s*)\((def\w*|ns)\s+([\w\-.?!*/]+)"))
    elif lang == "markdown":
        outline_markdown(text, out)
    elif lang == "json":
        outline_json(text, out)
    elif lang == "jsonl":
        out.notes.append(f"{out.n_lines} JSON records, one per line; first: {lines[0][:200]}")
    elif lang in ("yaml", "toml"):
        outline_yaml(text, out)
    elif lang == "csv":
        outline_csv(text, out)
    elif lang == "log":
        outline_log(text, out)
    elif lang == "shell":
        outline_shell(text, out)
    elif lang == "sql":
        outline_sql(text, out)
    elif lang == "html":
        outline_html(text, out)
    else:
        outline_text(text, out)
    if not out.entries and lang not in ("csv", "log", "jsonl") and not out.notes:
        outline_text(text, out)
    return out


def _fold_level(entries: List[Entry], level: int) -> List[Entry]:
    out: List[Entry] = []
    for e in entries:
        if e.depth == level and out:
            # attach to the nearest shallower entry before it
            for parent in reversed(out):
                if parent.depth < level:
                    names = parent.extra.split(", ") if parent.extra and not parent.extra.startswith(" @") else []
                    names.append(e.name.split("(")[0])
                    parent.extra = ", ".join(names)
                    break
            continue
        out.append(e)
    for e in out:
        if e.extra and not e.extra.startswith(" @") and e.extra != getattr(e, "_orig", None):
            names = e.extra.split(", ")
            if len(names) > 1 or e.depth < level:
                shown = names[:10]
                e.extra = f"{len(names)} member{'s' if len(names) != 1 else ''}: " + ", ".join(shown) + (" …" if len(names) > 10 else "")
    return out


def render_outline(o: Outline, max_entries: int = 70, show_path: bool = True) -> str:
    tokens = estimate_tokens("x" * o.n_chars)
    head = f"{o.path} — {o.n_lines:,} lines, ~{fmt_tokens(tokens)} tokens ({o.lang})" if show_path else \
        f"{o.n_lines:,} lines, ~{fmt_tokens(tokens)} tokens ({o.lang})"
    parts = [head]
    for n in o.notes:
        parts.append("  " + n)
    entries = list(o.entries)
    # Too many entries: fold the deepest level into its parents, level by level,
    # but never fold so far that fewer than max_entries/2 entries remain (a single
    # container holding everything is useless as a map). Whatever is still over
    # the limit is truncated with a note.
    truncated_note = ""
    while len(entries) > max_entries:
        deepest = max(e.depth for e in entries)
        if deepest == 0:
            break
        folded = _fold_level(entries, deepest)
        if len(folded) < max_entries // 2:
            break
        entries = folded
    if len(entries) > max_entries:
        shown = entries[:max_entries]
        rest = entries[max_entries:]
        truncated_note = (f"  … {len(rest)} more entries between lines {rest[0].start} and {rest[-1].end} "
                          f"(full map: `skimread outline --all`; or Grep for a name, then read that range)")
        entries = shown
    if truncated_note:
        parts.append(truncated_note)
    width = len(str(o.n_lines))
    for e in entries:
        rng = f"{e.start}-{e.end}" if e.end > e.start else f"{e.start}"
        indent = "  " * e.depth
        label = f"{e.kind} {e.name}" if e.kind not in ("h1", "h2", "h3", "h4", "h5", "h6", "key", "section", "stmt", "tag", "target", "step", "rule") else e.name
        if e.kind.startswith("h"):
            label = "#" * int(e.kind[1:]) + " " + e.name
        line = f"  {rng:>{width * 2 + 1}}  {indent}{label}"
        if e.extra:
            line += f"  ({e.extra.strip()})" if not e.extra.startswith(" @") else e.extra
        parts.append(line)
    return "\n".join(parts)


def outline_file(path: str, max_entries: int = 70) -> str:
    return render_outline(analyze(path), max_entries=max_entries)
