"""Generic, format-agnostic output reduction and budget fitting.

The pipeline is lossless by construction: the full normalised output is always
kept in the spool (see store.py) and every gap the digest introduces names the
line range it hides, so an agent can ask for exactly that slice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

# --- line model -------------------------------------------------------------

@dataclass
class Line:
    no: Optional[int]  # 1-based line number in the normalised full output; None = synthetic
    text: str
    score: int = 0  # -1 drop, 0 plain, 1 summary, 2 warning/frame, 3 error


def to_lines(text: str) -> List[Line]:
    if not text:
        return []
    return [Line(i + 1, t) for i, t in enumerate(text.split("\n"))]


# --- scoring ------------------------------------------------------------------

_ERR = re.compile(
    r"(?i)(?:\b(?:error|errors|exception|fatal|panic(?:ked)?|traceback|fail(?:ed|ure|ures|ing)?|"
    r"cannot|can't|could not|couldn't|not found|no such file|denied|refused|timed out|timeout|"
    r"unhandled|assert(?:ion)?(?:error)?|segmentation fault|abort(?:ed)?|unexpected|invalid|"
    r"undefined|unreachable|killed|oom|econnrefused|enoent|eacces|npm err!)\b|✖|✗|❌|^E\s{2,}|^\s*\^+\s*$)"
)
_FAIL_UPPER = re.compile(r"\b(?:FAIL|FAILED|ERROR|PANIC)\b")
_WARN = re.compile(r"(?i)\b(?:warn(?:ing)?s?|deprecat(?:ed|ion)|skipped|xfail)\b")
_FRAME = re.compile(
    r'^\s+at\s+\S|^\s+File ".+", line \d+|^\s*-->\s|^\s*\d+\s*\|\s|^\s+in\s+\S+\s+\(|^\S+\.(?:py|rs|go|ts|tsx|js|jsx|java|kt|rb|c|cpp|cc|h|cs|swift|php):\d+'
)
_SUMMARY = re.compile(
    r"^(?:=+ .* =+$|-+ .* -+$|Tests?:|Test Suites?:|Test Files|Snapshots?:|Time:|Duration|Ran \d+ tests?|"
    r"OK\b|Finished|Done in|Total|Summary|BUILD (?:SUCCESS|FAILURE|FAILED)|test result:|"
    r"\d+ passing|\d+ failing|\d+ pending|collected \d+|added \d+ packages|removed \d+ packages|"
    r"found \d+ vulnerabilit|Successfully installed|Installed \d+ packages|Resolved \d+ packages|"
    r"Compiled successfully|Build (?:succeeded|failed)|exit(?:ed)? (?:with )?(?:code|status))"
)
_PROGRESS = re.compile(
    r"(?:\[[=#>\-\s.]*\]\s*\d{1,3}%)|(?:\d{1,3}%\s*(?:\||$))|^[\s.]{20,}$|[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]|"
    r"^\s*[|/\-\\]\s*$|^\s*\d+(?:\.\d+)?\s*(?:[KMG]i?B)?/\d+(?:\.\d+)?\s*[KMG]i?B\b|\bETA\b|\beta\s+\d|^\s*[▇█▓░▒]+"
)


def score_line(text: str) -> int:
    if not text.strip():
        return 0
    if _PROGRESS.search(text):
        return -1
    if _ERR.search(text) or _FAIL_UPPER.search(text):
        return 3
    if _FRAME.search(text) or _WARN.search(text):
        return 2
    if _SUMMARY.search(text):
        return 1
    return 0


def score_all(lines: List[Line]) -> None:
    """Fill in scores for lines a format reducer left untouched (score 0)."""
    for ln in lines:
        if ln.no is not None and ln.score == 0:
            ln.score = score_line(ln.text)


# --- templating for near-duplicate collapse -----------------------------------

_TPL = [
    (re.compile(r"\b[0-9a-f]{7,}\b"), "#"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "#"),
    (re.compile(r"\d+(?:\.\d+)?"), "#"),
    (re.compile(r'"[^"]*"'), '""'),
    (re.compile(r"'[^']*'"), "''"),
]


def template(text: str) -> str:
    t = text
    for rx, rep in _TPL:
        t = rx.sub(rep, t)
    return t


# --- generic reduction ----------------------------------------------------------

def collapse(lines: List[Line], similar_min: int = 4, keep_verbatim: bool = False) -> List[Line]:
    """Drop progress noise, squeeze blank runs, fold exact and near duplicates.

    keep_verbatim disables near-duplicate folding (used for source-like output
    such as diffs and file dumps, where "similar" lines are real content).
    """
    out: List[Line] = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if ln.score < 0:
            i += 1
            continue
        if not ln.text.strip():
            if out and not out[-1].text.strip():
                i += 1
                continue
            out.append(ln)
            i += 1
            continue
        # exact duplicate run
        j = i + 1
        while j < n and lines[j].text == ln.text and lines[j].score >= 0:
            j += 1
        if j - i >= 2:
            cnt = j - i
            out.append(Line(ln.no, f"{ln.text}   (x{cnt}, lines {ln.no}-{lines[j - 1].no})", ln.score))
            i = j
            continue
        # near-duplicate run (same template), only for low-importance lines
        if not keep_verbatim and ln.score <= 1:
            tpl = template(ln.text)
            if tpl != ln.text:
                j = i + 1
                while j < n and lines[j].score >= 0 and lines[j].score <= 1 and template(lines[j].text) == tpl:
                    j += 1
                if j - i >= similar_min:
                    out.append(ln)
                    out.append(lines[i + 1])
                    hidden = j - i - 3
                    out.append(Line(None, f"  … {hidden} similar lines ({lines[i + 2].no}-{lines[j - 2].no}) …", 0))
                    out.append(lines[j - 1])
                    i = j
                    continue
        out.append(ln)
        i += 1
    return out


# --- budget fitting -------------------------------------------------------------

def _chars(lines: List[Line]) -> int:
    return sum(len(l.text) + 1 for l in lines)


def fit_budget(lines: List[Line], budget: int, head: int = 8, tail: int = 20, context: int = 2,
               handle: str = "") -> List[Line]:
    """Return a subset of lines whose rendered size fits budget chars.

    Always prefers: the tail (summaries live there), the head (the command's
    own banner), then windows around the highest scored lines. Cut regions are
    replaced by a gap marker naming the hidden line range.
    """
    if _chars(lines) <= budget:
        return lines
    n = len(lines)
    keep = [False] * n
    for k in range(min(head, n)):
        keep[k] = True
    for k in range(max(0, n - tail), n):
        keep[k] = True

    def used() -> int:
        return sum(len(lines[k].text) + 1 for k in range(n) if keep[k])

    # windows around important lines, best first
    order = sorted((k for k in range(n) if lines[k].score >= 2), key=lambda k: (-lines[k].score, k))
    for k in order:
        lo, hi = max(0, k - context), min(n, k + context + 1)
        add = sum(len(lines[m].text) + 1 for m in range(lo, hi) if not keep[m])
        if used() + add > budget:
            if not keep[k] and used() + len(lines[k].text) + 1 <= budget:
                keep[k] = True
            continue
        for m in range(lo, hi):
            keep[m] = True
    # if head+tail alone overflow, trim the head
    k = min(head, n) - 1
    while used() > budget and k >= 0:
        keep[k] = False
        k -= 1
    # then trim the tail from its start
    k = max(0, n - tail)
    while used() > budget and k < n - 3:
        keep[k] = False
        k += 1

    out: List[Line] = []
    k = 0
    while k < n:
        if keep[k]:
            out.append(lines[k])
            k += 1
            continue
        j = k
        while j < n and not keep[j]:
            j += 1
        nos = [lines[m].no for m in range(k, j) if lines[m].no is not None]
        if nos:
            a, b = nos[0], nos[-1]
            hint = f" — {handle} {a}-{b}" if handle else ""
            out.append(Line(None, f"… [{b - a + 1} lines omitted ({a}-{b}){hint}] …", 0))
        k = j
    return out


def render(lines: List[Line]) -> str:
    return "\n".join(l.text for l in lines)
