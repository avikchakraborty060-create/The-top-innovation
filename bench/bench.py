"""Measure skimread on the real-world fixtures in tests/fixtures.

    python bench/bench.py            # markdown table
    python bench/bench.py --json
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SKIMREAD_HOME", os.path.join(os.path.dirname(__file__), ".bench-home"))

from skimread.digest import build_digest  # noqa: E402
from skimread.outline import analyze, render_outline  # noqa: E402
from skimread.tokens import estimate_tokens  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")
CASES = [
    ("pytest (65 tests, 3 failing, 60 warnings)", "pytest_default.txt", "pytest test_sample.py"),
    ("pytest -v", "pytest_verbose.txt", "pytest -v test_sample.py"),
    ("pytest -v (1,504 tests, 2 failing)", "pytest_big_verbose.txt", "pytest -v test_many.py"),
    ("go test -v (1 fail, 1 panic)", "go_test.txt", "go test -v ./..."),
    ("cargo test (1 fail, backtrace)", "cargo_test.txt", "cargo test"),
    ("cargo build (2 errors)", "cargo_build_err.txt", "cargo build"),
    ("vitest (13 files, 3 failing)", "vitest.txt", "npx vitest run"),
    ("vitest --reporter=verbose (579 tests, 3 failing)", "vitest_verbose.txt", "npx vitest run --reporter=verbose"),
    ("cargo build (37 crates, 3 warnings)", "cargo_build_deps.txt", "cargo build"),
    ("tsc (48 errors in 8 files)", "tsc.txt", "npx tsc -p tsconfig.json"),
    ("npm install (2 packages)", "npm_install.txt", "npm install --save-dev vitest typescript@5"),
    ("npm install (649 packages, 23 deprecation warnings)", "npm_install_big.txt", "npm install express@4 webpack@4 request@2 gulp@3"),
]


def main():
    rows = []
    for label, name, cmd in CASES:
        raw = open(os.path.join(FIX, name), encoding="utf-8").read()
        d = build_digest(raw, cmd=cmd, exit_code=1, save=False)
        rows.append((label, raw.count("\n"), estimate_tokens(raw), d.tokens_out))
    # outline vs full read, on this repo's own sources and a few generated shapes
    reads = []
    for path in ["skimread/outline.py", "skimread/formats.py", "skimread/cli.py"]:
        p = os.path.join(os.path.dirname(__file__), "..", path)
        text = open(p, encoding="utf-8").read()
        reads.append((path, text.count("\n"), estimate_tokens(text), estimate_tokens(render_outline(analyze(p, text)))))
    if "--json" in sys.argv:
        print(json.dumps({"commands": rows, "reads": reads}, indent=2))
        return
    print("| Command output | lines | tokens in | tokens out | saved |")
    print("|---|---:|---:|---:|---:|")
    for label, n, ti, to in rows:
        print(f"| {label} | {n} | {ti:,} | {to:,} | {100 * (ti - to) / ti:.0f}% |")
    print()
    print("| File read | lines | full read (tokens) | outline (tokens) | saved if you then read a 60-line slice |")
    print("|---|---:|---:|---:|---:|")
    for path, n, ti, to in reads:
        slice_tok = int(ti / max(1, n) * 60)
        print(f"| {path} | {n} | {ti:,} | {to:,} | {100 * (ti - to - slice_tok) / ti:.0f}% |")


if __name__ == "__main__":
    main()
