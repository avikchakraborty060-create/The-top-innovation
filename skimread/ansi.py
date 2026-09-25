"""Terminal output normalisation: ANSI escapes, carriage returns, backspaces."""

import re

_CSI = re.compile(r"\x1b\[[0-9;?<>=!]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ESC_OTHER = re.compile(r"\x1b[@-Z\\-_]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_ansi(text: str) -> str:
    text = _OSC.sub("", text)
    text = _CSI.sub("", text)
    text = _ESC_OTHER.sub("", text)
    return text


def _resolve_cr(line: str) -> str:
    """Emulate a terminal: '\r' returns to column 0 and later text overwrites."""
    if "\r" not in line:
        return line
    parts = line.split("\r")
    # Keep the last non-empty segment; progress bars rewrite the same line.
    out = ""
    for seg in parts:
        if not seg:
            continue
        if len(seg) >= len(out):
            out = seg
        else:
            out = seg + out[len(seg):]
    return out


def normalize(raw: str) -> str:
    """Return plain text with one logical line per terminal line."""
    text = raw.replace("\r\n", "\n")
    text = strip_ansi(text)
    lines = []
    for line in text.split("\n"):
        line = _resolve_cr(line)
        if "\x08" in line:
            buf = []
            for ch in line:
                if ch == "\x08":
                    if buf:
                        buf.pop()
                else:
                    buf.append(ch)
            line = "".join(buf)
        line = _CTRL.sub("", line)
        lines.append(line.rstrip())
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)
