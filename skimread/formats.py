"""Format-aware reducers for common developer tools.

Each reducer receives the normalised lines of one command's output and returns
a reduced list. Reducers only *drop or fold* lines that carry no decision value
for an agent (progress, per-item PASS lines, download chatter) and may add
synthetic summary lines (no=None). Everything else flows through the generic
scorer and the budget fitter. The full output stays in the spool regardless.
"""

from __future__ import annotations

import re
import shlex
from typing import Callable, List, Optional, Tuple

from .reduce import Line, collapse, score_all

Reducer = Callable[[List[Line], "Ctx"], List[Line]]


class Ctx:
    def __init__(self, cmd: str = "", budget: int = 6000, handle: str = ""):
        self.cmd = cmd
        self.budget = budget
        self.handle = handle


def _argv(cmd: str) -> List[str]:
    try:
        toks = shlex.split(cmd, posix=True)
    except ValueError:
        toks = cmd.split()
    # skip leading env assignments and common runners
    while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
        toks.pop(0)
    while toks and toks[0] in ("time", "nice", "env", "sudo", "uvx", "npx", "pnpx", "bunx", "poetry", "pipenv"):
        toks.pop(0)
        if toks and toks[0] == "run" and len(toks) > 1:
            toks.pop(0)
    if len(toks) >= 3 and toks[0] in ("python", "python3", "py") and toks[1] == "-m":
        toks = toks[2:]
    return toks


def _head_text(lines: List[Line], n: int = 40) -> str:
    return "\n".join(l.text for l in lines[:n])


def _synthetic(text: str, score: int = 1) -> Line:
    return Line(None, text, score)


def _fold(lines: List[Line], pattern: "re.Pattern[str]", label: str, keep_first: int = 0) -> List[Line]:
    """Remove lines matching pattern; append one synthetic count line at the
    position of the first removed line."""
    out: List[Line] = []
    removed = 0
    first_pos = None
    kept = 0
    for ln in lines:
        if ln.no is not None and pattern.search(ln.text):
            if kept < keep_first:
                kept += 1
                out.append(ln)
                continue
            removed += 1
            if first_pos is None:
                first_pos = len(out)
            continue
        out.append(ln)
    if removed:
        out.insert(first_pos or 0, _synthetic(f"  ({removed} {label} hidden)", 1))
    return out


# --- pytest -----------------------------------------------------------------------

_PY_PROGRESS = re.compile(r"^\S+\.py(?:::\S+)?\s+[.FsxXE%\[\] \d]+$|^\S+\.py\s*$")
_PY_META = re.compile(r"^(?:platform |cachedir: |rootdir: |configfile: |plugins: |collecting |testpaths: )")
_PY_SECTION = re.compile(r"^=+ (.*?) =+$")
_PY_VERBOSE_PASS = re.compile(r"^\S+::\S+(?: \S+)? (?:PASSED|SKIPPED|XFAIL|XPASS)\b")


def reduce_pytest(lines: List[Line], ctx: Ctx) -> List[Line]:
    out: List[Line] = []
    in_warnings = False
    warn_hidden = 0
    passed_hidden = 0
    passed_pos = None
    for ln in lines:
        t = ln.text
        if _PY_VERBOSE_PASS.match(t):
            passed_hidden += 1
            if passed_pos is None:
                passed_pos = len(out)
            continue
        m = _PY_SECTION.match(t)
        if m:
            sec = m.group(1).lower()
            if in_warnings and warn_hidden:
                out.append(_synthetic(f"  ({warn_hidden} warning lines hidden)", 1))
            in_warnings = sec.startswith("warnings summary")
            warn_hidden = 0
            if "failed" in sec or "passed" in sec or "error" in sec or "no tests" in sec:
                ln.score = 3
            elif "short test summary" in sec or "failures" in sec or "errors" in sec:
                ln.score = 3
            else:
                ln.score = 1
            out.append(ln)
            continue
        if in_warnings:
            warn_hidden += 1
            continue
        if _PY_META.match(t) or _PY_PROGRESS.match(t):
            continue
        if t.startswith("collected "):
            ln.score = 1
        elif t.startswith("E ") or t.startswith("FAILED ") or t.startswith("ERROR "):
            ln.score = 3
        elif t.startswith(">") or re.match(r"^\S+\.py:\d+", t) or re.match(r"^_+ .* _+$", t):
            ln.score = 2
        out.append(ln)
    if in_warnings and warn_hidden:
        out.append(_synthetic(f"  ({warn_hidden} warning lines hidden)", 1))
    if passed_hidden:
        out.insert(passed_pos or 0, _synthetic(f"  ({passed_hidden} passing/skipped tests hidden)", 1))
    score_all(out)
    return collapse(out)


# --- jest / vitest / mocha ---------------------------------------------------------

_JS_PASS = re.compile(r"^\s*(?:PASS|✓|√)\s|^\s*✓\s|^\s*[✔√]\s")
_JS_FAIL = re.compile(r"^\s*(?:FAIL|✕|×|✗|❯)\s")


def reduce_jest(lines: List[Line], ctx: Ctx) -> List[Line]:
    out: List[Line] = []
    hidden = 0
    first_pos = None
    for ln in lines:
        t = ln.text
        if _JS_PASS.match(t) and "fail" not in t.lower():
            hidden += 1
            if first_pos is None:
                first_pos = len(out)
            continue
        if _JS_FAIL.match(t):
            ln.score = 3
        elif t.startswith("  ●") or t.startswith("●"):
            ln.score = 3
        elif re.match(r"^\s*(?:Tests?|Test Suites?|Test Files|Snapshots?|Time|Duration|Start at):", t):
            ln.score = 3
        out.append(ln)
    if hidden:
        out.insert(first_pos or 0, _synthetic(f"  ({hidden} passing tests/suites hidden)", 1))
    score_all(out)
    return collapse(out)


# --- cargo -------------------------------------------------------------------------------

_CARGO_NOISE = re.compile(r"^\s*(?:Compiling|Checking|Downloading|Downloaded|Updating|Fresh|Locking|Adding|Blocking|Documenting|Building|Installing)\s")
_CARGO_TEST_OK = re.compile(r"^test \S+ \.\.\. (?:ok|ignored)")


_RUST_STD_FRAME = re.compile(r"^\s+\d+: (?:core::|std::|alloc::|__rustc|rust_begin_unwind|<core::|<alloc::|<std::|test::|tokio::runtime)")
_RUST_STD_AT = re.compile(r"^\s+at (?:/rustc/|\S*/library/(?:core|std|alloc)/)")


def _fold_frames(lines: List[Line], frame_rx: "re.Pattern[str]", at_rx: "re.Pattern[str]", label: str) -> List[Line]:
    """Drop runtime/stdlib stack frames (and their 'at' lines); keep user frames."""
    out: List[Line] = []
    hidden = 0
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.no is not None and frame_rx.match(ln.text):
            hidden += 1
            i += 1
            while i < len(lines) and lines[i].no is not None and at_rx.match(lines[i].text):
                hidden += 1
                i += 1
            if not out or out[-1].text != f"      ({label} frames hidden)":
                out.append(_synthetic(f"      ({label} frames hidden)", 0))
            continue
        out.append(ln)
        i += 1
    if hidden:
        for ln in out:
            if ln.no is None and ln.text == f"      ({label} frames hidden)":
                ln.text = f"      (…{label} frames hidden)"
    return out


def reduce_cargo(lines: List[Line], ctx: Ctx) -> List[Line]:
    out = _fold(lines, _CARGO_NOISE, "compile/download lines")
    out = _fold(out, _CARGO_TEST_OK, "passing tests")
    out = _fold_frames(out, _RUST_STD_FRAME, _RUST_STD_AT, "std")
    for ln in out:
        t = ln.text
        if ln.no is None:
            continue
        if t.startswith("error") or t.startswith("test result: FAILED") or " FAILED" in t or t == "failures:":
            ln.score = 3
        elif t.startswith("warning") or t.lstrip().startswith("-->"):
            ln.score = 2
        elif t.startswith("test result:") or t.lstrip().startswith("Finished") or t.lstrip().startswith("Running"):
            ln.score = 1
    score_all(out)
    return collapse(out)


# --- go ------------------------------------------------------------------------------------

_GO_NOISE = re.compile(r"^(?:ok\s+\S+\s+[\d.]+s|\?\s+\S+\s+\[no test files\]|=== (?:RUN|PAUSE|CONT)|\s*--- PASS)")


_GO_RT_FRAME = re.compile(r"^(?:testing\.|runtime\.|panic\(|created by testing\.|reflect\.|sync\.|net/http\.)")
_GO_RT_AT = re.compile(r"^\t\S*/go\S*/src/|^\t/usr/local/go/|^\t\S*/pkg/mod/")


def reduce_go(lines: List[Line], ctx: Ctx) -> List[Line]:
    out = _fold(lines, _GO_NOISE, "passing packages/tests")
    out = _fold_frames(out, _GO_RT_FRAME, _GO_RT_AT, "runtime")
    for ln in out:
        if ln.no is None:
            continue
        t = ln.text
        if t.startswith("--- FAIL") or t.startswith("FAIL") or t.startswith("panic:") or re.search(r"\.go:\d+:", t):
            ln.score = 3
        elif t.startswith("PASS") or t.startswith("ok"):
            ln.score = 1
    score_all(out)
    return collapse(out)


# --- npm / yarn / pnpm / bun --------------------------------------------------------------

_NPM_WARN = re.compile(r"^npm (?:WARN|warn|notice)\b|^warning (?:\S+@|.*(?:deprecated|peer))|^\s*WARN\b")


def reduce_npm(lines: List[Line], ctx: Ctx) -> List[Line]:
    out = _fold(lines, _NPM_WARN, "npm warnings", keep_first=2)
    for ln in out:
        if ln.no is None:
            continue
        t = ln.text
        if t.startswith("npm ERR!") or t.startswith("npm error") or t.startswith("error "):
            ln.score = 3
        elif re.match(r"^(?:added|removed|changed|up to date|audited|found \d+|\d+ (?:high|moderate|low|critical)|Done in|Progress:)", t):
            ln.score = 1
    score_all(out)
    return collapse(out)


# --- pip / uv / poetry -----------------------------------------------------------------

_PIP_NOISE = re.compile(
    r"^\s*(?:Collecting |Downloading |Using cached |Requirement already satisfied|Preparing metadata|"
    r"Building wheel|Created wheel|Stored in directory|Attempting uninstall|Uninstalling |"
    r"Getting requirements|Installing build dependencies|Running command|\s+Prepared |\s+Built |"
    r"\s+Uninstalled |\s+[+\-~] \S+==|\s+[+\-~] \S+ v?\d)"
)


def reduce_pip(lines: List[Line], ctx: Ctx) -> List[Line]:
    out = _fold(lines, _PIP_NOISE, "download/resolve lines")
    for ln in out:
        if ln.no is None:
            continue
        t = ln.text
        if t.startswith("ERROR") or t.startswith("error:") or "error" in t.lower():
            ln.score = 3
        elif t.startswith("Successfully installed") and len(t) > 300:
            ln.text = t[:280] + " …"
            ln.score = 1
        elif re.match(r"^(?:Successfully|Installed \d+|Resolved \d+|Audited \d+|Installing collected)", t):
            ln.score = 1
    score_all(out)
    return collapse(out)


# --- docker (BuildKit) -----------------------------------------------------------------------

_DOCKER_NOISE = re.compile(
    r"^#\d+ (?:DONE|CACHED|sha256:|resolve |extracting |transferring |exporting |writing |naming |"
    r"preparing |unpacking |merging |sending )|^#\d+ \.\.\.$|^\s*=> (?:\[internal\]|CACHED|=> )"
)


def reduce_docker(lines: List[Line], ctx: Ctx) -> List[Line]:
    out = _fold(lines, _DOCKER_NOISE, "build step status lines")
    for ln in out:
        if ln.no is None:
            continue
        t = ln.text
        if re.match(r"^#\d+ \[[^\]]+\] ", t):
            ln.score = 1
        elif t.startswith("#") and ("ERROR" in t or "error" in t):
            ln.score = 3
        elif t.startswith("------") or t.startswith("Dockerfile:") or t.startswith("ERROR:"):
            ln.score = 3
    score_all(out)
    return collapse(out)


# --- tsc / eslint / generic compilers --------------------------------------------------

_TSC_ERR = re.compile(r"^(\S+?)\(\d+,\d+\): error TS\d+")


def reduce_tsc(lines: List[Line], ctx: Ctx) -> List[Line]:
    errs = [ln for ln in lines if _TSC_ERR.match(ln.text)]
    if len(errs) <= 25:
        score_all(lines)
        return collapse(lines, keep_verbatim=True)
    out: List[Line] = []
    shown = 0
    per_file: dict = {}
    for ln in lines:
        m = _TSC_ERR.match(ln.text)
        if m:
            per_file[m.group(1)] = per_file.get(m.group(1), 0) + 1
            if shown < 25:
                shown += 1
                ln.score = 3
                out.append(ln)
            continue
        if shown and ln.text.startswith(" "):
            if shown <= 25 and out and out[-1].score == 3:
                out.append(ln)
            continue
        out.append(ln)
    files = sorted(per_file.items(), key=lambda kv: -kv[1])
    rest = len(errs) - shown
    summary = ", ".join(f"{f} ({c})" for f, c in files[:12])
    if len(files) > 12:
        summary += f", … {len(files) - 12} more files"
    out.append(_synthetic(f"  … {rest} more errors not shown. Errors per file: {summary}", 3))
    score_all(out)
    return out


# --- git diff ----------------------------------------------------------------------------------

def reduce_gitdiff(lines: List[Line], ctx: Ctx) -> List[Line]:
    """Whole files in order until the budget runs out, then a diffstat for the rest."""
    total = sum(len(l.text) + 1 for l in lines)
    if total <= ctx.budget:
        return lines
    blocks: List[Tuple[str, List[Line]]] = []
    cur: List[Line] = []
    name = ""
    for ln in lines:
        if ln.text.startswith("diff --git"):
            if cur:
                blocks.append((name, cur))
            cur = []
            parts = ln.text.split(" b/")
            name = parts[-1] if len(parts) > 1 else ln.text
        cur.append(ln)
    if cur:
        blocks.append((name, cur))
    if len(blocks) <= 1:
        score_all(lines)
        return lines  # a single big file: let the budget fitter cut with handles
    out: List[Line] = []
    used = 0
    rest: List[Tuple[str, int, int, int]] = []
    for name, blk in blocks:
        size = sum(len(l.text) + 1 for l in blk)
        if used + size <= ctx.budget * 0.8 and not rest:
            out.extend(blk)
            used += size
        else:
            plus = sum(1 for l in blk if l.text.startswith("+") and not l.text.startswith("+++"))
            minus = sum(1 for l in blk if l.text.startswith("-") and not l.text.startswith("---"))
            rest.append((name, plus, minus, blk[0].no or 0))
    if rest:
        out.append(_synthetic(f"  … {len(rest)} more files not shown (full diff: {ctx.handle}):", 1))
        for name, plus, minus, start in rest:
            out.append(_synthetic(f"    {name}  +{plus} -{minus}  (from line {start})", 1))
    return out


# --- verbatim-ish (file dumps, listings, grep) ------------------------------------------

def reduce_verbatim(lines: List[Line], ctx: Ctx) -> List[Line]:
    # File content is not a log: "error" in source code is not important, no line
    # is progress noise, and identical neighbours (closing braces) are real content.
    for ln in lines:
        ln.score = 0
    return collapse(lines, keep_verbatim=True, dup_min=5)


def reduce_find(lines: List[Line], ctx: Ctx) -> List[Line]:
    total = sum(len(l.text) + 1 for l in lines)
    for ln in lines:
        ln.score = 0
    if total <= ctx.budget:
        return lines
    counts: dict = {}
    for ln in lines:
        p = ln.text.strip()
        top = "/".join(p.split("/")[:2]) if "/" in p else p
        counts[top] = counts.get(top, 0) + 1
    tops = sorted(counts.items(), key=lambda kv: -kv[1])[:15]
    out = [_synthetic(f"  {len(lines)} entries. Largest groups: " + ", ".join(f"{k} ({v})" for k, v in tops), 1)]
    out.extend(lines)
    return out


def reduce_generic(lines: List[Line], ctx: Ctx) -> List[Line]:
    score_all(lines)
    return collapse(lines)


# --- detection -------------------------------------------------------------------------------

_TEST_RUNNERS = {"jest", "vitest", "mocha", "ava", "karma", "cypress", "playwright"}


def detect(cmd: str, lines: List[Line]) -> Tuple[str, Reducer]:
    """Pick a reducer from the command line first, then from the output shape."""
    argv = _argv(cmd)
    a0 = argv[0] if argv else ""
    a1 = argv[1] if len(argv) > 1 else ""
    base = a0.rsplit("/", 1)[-1]
    head = _head_text(lines)

    if base in ("pytest", "py.test") or "= test session starts =" in head:
        return "pytest", reduce_pytest
    if base in _TEST_RUNNERS or (base in ("npm", "yarn", "pnpm", "bun") and a1 in ("test", "t", "run") and (
        "PASS " in head or "FAIL " in head or "✓" in head or "Test Files" in head or "RUN  v" in head)):
        return "jest", reduce_jest
    if base == "cargo":
        return "cargo", reduce_cargo
    if base == "go" and a1 in ("test", "build", "vet", "run", "install", "generate"):
        return "go", reduce_go
    if base in ("npm", "yarn", "pnpm", "bun") and a1 in ("install", "i", "ci", "add", "update", "up", "remove", "rm", "uninstall", "audit", ""):
        return "npm", reduce_npm
    if base in ("pip", "pip3", "uv", "poetry", "pipenv", "conda") or (base in ("python", "python3") and "pip" in cmd):
        return "pip", reduce_pip
    if base in ("docker", "docker-compose", "podman") or "#1 [internal] load" in head:
        return "docker", reduce_docker
    if base == "tsc" or re.search(r"^\S+\(\d+,\d+\): error TS\d+", head, re.M):
        return "tsc", reduce_tsc
    if base == "git" and a1 in ("diff", "show", "log"):
        return "gitdiff" if a1 != "log" else "verbatim", reduce_gitdiff if a1 != "log" else reduce_verbatim
    if base in ("cat", "head", "tail", "bat", "less", "more", "sed", "awk", "grep", "rg", "ag", "jq", "yq", "diff"):
        return "verbatim", reduce_verbatim
    if base in ("find", "ls", "tree", "fd", "du") or (base == "git" and a1 == "ls-files"):
        return "find", reduce_find
    if "= test session starts =" in head:
        return "pytest", reduce_pytest
    if re.search(r"^(?:PASS|FAIL) \S+\.(?:test|spec)\.", head, re.M):
        return "jest", reduce_jest
    if re.search(r"^\s+Compiling \S+ v\d", head, re.M):
        return "cargo", reduce_cargo
    return "generic", reduce_generic
