# Contributing to skimread

Thank you. The most valuable contribution is a **real output sample** from a tool skimread handles badly, because every reducer is only as good as the outputs it has seen.

## Send a fixture (no code needed)

1. Run the command with colours off and capture everything: `NO_COLOR=1 <command> > sample.txt 2>&1`
2. Remove anything private (paths, hostnames, tokens).
3. Open an issue with the "Output sample" template and attach the file, or open a pull request that drops it into `tests/fixtures/`.

## Add a format reducer

1. Write `reduce_<tool>()` in `skimread/formats.py`. It receives the normalised lines and returns a reduced list. Only drop or fold lines that carry no decision value (progress, per-item PASS lines, download chatter) and set scores on lines that must survive (3 = error, 2 = warning or stack frame, 1 = summary).
2. Register it in `detect()` by command name and, if possible, by output shape, so it also works through `skimread pipe`.
3. Add the fixture to `tests/fixtures/` and a row to `tests/test_formats.py` stating what must survive and what must not.
4. Run `python -m pytest -q` and `python bench/bench.py`.

## Add an outline language

1. Add an entry to `_LANG_BY_EXT` in `skimread/outline.py`.
2. For brace languages, add a pattern list to `_BRACE_PATTERNS`; for indentation languages, call `outline_indent` with a declaration regex; for data formats, write a small `outline_<kind>()`.
3. Add a test in `tests/test_outline.py` with a short realistic source and the expected names, ranges and depths.

## Ground rules

- No dependencies. The package must keep installing with nothing but the standard library.
- Lossless. A change may hide lines, never rewrite them, and the full output must stay reachable through `skimread show`.
- Keep the hook safe. Anything that widens what the Bash wrapper touches needs a test in `tests/test_hooks_cli.py`.
- Python 3.9 compatible.

Run the whole suite with `python -m pytest -q`; it takes under two seconds.
