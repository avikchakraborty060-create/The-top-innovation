from skimread.ansi import normalize, strip_ansi
from skimread.reduce import Line, collapse, fit_budget, render, score_all, score_line, to_lines


def test_strip_ansi_and_cr():
    raw = "\x1b[32mPASS\x1b[0m one\r\nprogress 10%\rprogress 50%\rprogress 100%\n\x1b]0;title\x07done"
    assert normalize(raw) == "PASS one\nprogress 100%\ndone"
    assert strip_ansi("\x1b[1;31mx\x1b[m") == "x"


def test_backspace_and_trailing_blank_lines():
    assert normalize("abc\x08d\n\n\n") == "abd"


def test_scores():
    assert score_line("Error: boom") == 3
    assert score_line("FAIL src/x.test.ts") == 3
    assert score_line("warning: unused variable") == 2
    assert score_line('  File "x.py", line 3') == 2
    assert score_line("[=====>    ] 45%") == -1
    assert score_line("Tests: 10 passed") == 1
    assert score_line("Tests: 3 failed, 10 passed") == 3
    assert score_line("plain text") == 0


def test_collapse_exact_and_similar():
    text = "\n".join(["same"] * 5 + [f"Downloading pkg-{i} 1.{i}.0" for i in range(10)] + ["", "", "end"])
    lines = to_lines(text)
    score_all(lines)
    out = render(collapse(lines))
    assert "same   (x5, lines 1-5)" in out
    assert "similar lines" in out
    assert "Downloading pkg-0 1.0.0" in out and "Downloading pkg-9 1.9.0" in out
    assert "\n\n\n" not in out and "\n\nend" in out  # blank run squeezed to one
    assert out.endswith("end")


def test_collapse_never_folds_errors():
    lines = to_lines("\n".join(f"ERROR test_{i} failed" for i in range(8)))
    score_all(lines)
    out = render(collapse(lines))
    assert out.count("ERROR") == 8


def test_verbatim_keeps_similar_lines():
    lines = to_lines("\n".join(f"x[{i}] = {i};" for i in range(10)))
    out = collapse(lines, keep_verbatim=True)
    assert len(out) == 10


def test_fit_budget_keeps_errors_and_names_gaps():
    body = [f"line {i}" for i in range(1, 401)]
    body[199] = "Error: the important one"
    lines = to_lines("\n".join(body))
    score_all(lines)
    fitted = fit_budget(lines, budget=600, head=3, tail=3, context=1, handle="skimread show 7")
    out = render(fitted)
    assert "Error: the important one" in out
    assert "line 199" in out and "line 201" in out  # context around the error
    assert "line 1\n" in out and out.endswith("line 400")
    assert "omitted (4-198) — skimread show 7 4-198" in out
    assert len(out) <= 600 + 200  # gap markers are the only overhead


def test_fit_budget_noop_when_small():
    lines = to_lines("a\nb\nc")
    assert fit_budget(lines, 100) is lines
