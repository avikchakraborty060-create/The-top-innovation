import pytest

from skimread.digest import build_digest
from skimread.formats import detect
from skimread.reduce import to_lines
from skimread.tokens import estimate_tokens
from tests.conftest import fixture


@pytest.mark.parametrize("name,cmd,fmt,must_have,must_not,min_ratio", [
    ("pytest_default.txt", "pytest test_sample.py", "pytest",
     ["3 failed, 62 passed", "FAILED test_sample.py::test_math", "ZeroDivisionError", "warning lines hidden"],
     ["DeprecationWarning: deprecated thing 5", "rootdir:"], 0.75),
    ("pytest_verbose.txt", "python -m pytest -v", "pytest",
     ["3 failed", "test_param_fail[1]", "one is bad"], ["test_ok[17] PASSED"], 0.75),
    ("pytest_big_verbose.txt", "pytest -v test_many.py", "pytest",
     ["2 failed, 1502 passed", "role mismatch for user 3", "passing/skipped tests hidden", "test_some_fail[1] FAILED"],
     ["test_many[7] PASSED"], 0.9),
    ("vitest_verbose.txt", "npx vitest run --reporter=verbose", "jest",
     ["passing tests/suites hidden", "Tests  3 failed | 576 passed", "Cannot read properties of null"], ["module 77 > adds"], 0.7),
    ("cargo_build_deps.txt", "cargo build", "cargo",
     ["compile/download lines hidden", "unused variable: `x`", "Finished `dev` profile"], ["Compiling regex"], 0.5),
    ("npm_install_big.txt", "npm install express@4 webpack@4 request@2 gulp@3", "npm",
     ["added 649 packages", "36 vulnerabilities", "npm warnings hidden"], ["npm warn deprecated fsevents"], 0.6),
    ("go_test.txt", "go test -v ./...", "go",
     ["--- FAIL: TestFails", "a_test.go:6: expected 1, got 2", "panic: assignment to entry in nil map", "a_test.go:7"],
     ["=== RUN   TestA", "testing.go:1734"], 0.5),
    ("cargo_test.txt", "cargo test", "cargo",
     ["test tests::bad ... FAILED", "math is hard", "src/lib.rs:10:24", "test result: FAILED"],
     ["test tests::ok1 ... ok", "core::panicking::panic_fmt"], 0.3),
    ("cargo_build_err.txt", "cargo build", "cargo",
     ["error[E0308]: mismatched types", "src/broken.rs:1:42", "could not compile"], ["Compiling sample"], -0.05),
    ("vitest.txt", "npx vitest run", "jest",
     ["FAIL  src/bad.test.ts > bad > fails on math", "Tests  3 failed | 36 passed", "expected 42 to be 43"], [], -0.05),
    ("tsc.txt", "npx tsc -p tsconfig.json", "tsc",
     ["ts/f1.ts(1,42): error TS2322", "more errors not shown", "ts/f8.ts (6)"], ["ts/f8.ts(5,43)"], 0.4),
    ("npm_install.txt", "npm install --save-dev vitest typescript@5", "npm",
     ["added 36 packages", "found 0 vulnerabilities"], ["npm notice Changelog"], 0.0),
])
def test_fixture_digest(name, cmd, fmt, must_have, must_not, min_ratio):
    raw = fixture(name)
    assert detect(cmd, to_lines(raw))[0] == fmt
    d = build_digest(raw, cmd=cmd, exit_code=1, save=False)
    for s in must_have:
        assert s in d.text, f"missing {s!r} in digest:\n{d.text}"
    for s in must_not:
        assert s not in d.text, f"unexpected {s!r} in digest:\n{d.text}"
    ratio = 1 - estimate_tokens(d.text) / max(1, estimate_tokens(raw))
    assert ratio >= min_ratio, f"only {ratio:.0%} reduction for {name}"


def test_detection_by_content_without_command():
    raw = fixture("pytest_default.txt")
    assert detect("", to_lines(raw))[0] == "pytest"
    raw = fixture("cargo_test.txt")
    assert detect("make check", to_lines(raw))[0] == "cargo"


def test_git_diff_budget_lists_remaining_files():
    files = []
    for i in range(12):
        files.append(f"diff --git a/f{i}.py b/f{i}.py\n--- a/f{i}.py\n+++ b/f{i}.py\n@@ -1,3 +1,4 @@\n" +
                     "\n".join(f"+added line {j} in file {i} with some padding text" for j in range(30)) + "\n-removed\n")
    raw = "".join(files)
    d = build_digest(raw, cmd="git diff", exit_code=0, save=False, budget=3000)
    assert d.fmt == "gitdiff"
    assert "diff --git a/f0.py b/f0.py" in d.text
    assert "more files not shown" in d.text
    assert "f11.py  +30 -1" in d.text


def test_generic_unknown_tool_progress_and_noise():
    raw = "\n".join(["[====>     ] 40%", "[=======>  ] 70%"] + [f"processing item {i}" for i in range(50)] +
                    ["Traceback (most recent call last):", '  File "x.py", line 1, in <module>', "ValueError: bad"])
    d = build_digest(raw, cmd="./my-custom-tool", exit_code=1, save=False)
    assert d.fmt == "generic"
    assert "40%" not in d.text
    assert "ValueError: bad" in d.text and 'File "x.py"' in d.text
    assert "similar lines" in d.text


def test_find_summary_over_budget():
    raw = "\n".join(f"./src/pkg{i % 5}/file{i}.py" for i in range(2000))
    d = build_digest(raw, cmd="find . -name '*.py'", exit_code=0, save=False, budget=2000)
    assert "2000 entries" in d.text and "./src (2000)" in d.text
    assert "lines omitted" in d.text
