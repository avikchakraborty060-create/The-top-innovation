# skimread — skim before you read

**A lossless context guard for AI coding agents.** Zero dependencies, runs locally, works with Claude Code out of the box and with any other agent (Codex, Cursor, Aider, your own) through a CLI and an MCP server.

AI coding agents burn most of their tokens on two things:

1. **Reading big files whole.** The agent wants one function and reads 2,000 lines to find it. Every session, again.
2. **Noisy command output.** `pytest -v` prints 1,500 `PASSED` lines to tell you about 2 failures. `npm install` prints 40 deprecation warnings. `cargo build` lists 37 crates it compiled.

Tools that compress shell output already exist (RTK, token-saver, condense) and they all share two problems. First, they cannot touch the agent's own file-reading tool, which is the bigger sink. Second, they are lossy: when the compressor dropped the one line the agent needed, the agent re-runs the command, and an [independent benchmark](https://blog.jetbrains.com/ai/2026/07/rtk-claude-code-token-savings/) found that this can cost *more* than not compressing at all.

skimread fixes both:

- **The Read guard.** When Claude Code is about to read a large file without a line range, skimread hands it a structural outline instead (symbols, headings, keys, with line ranges) and says "read the slice you need". The agent then reads 60 lines instead of 2,000. Any ranged read is always allowed, and if the agent insists on the whole file it gets it.
- **Lossless digests.** Every command's full output is kept locally. The digest keeps errors, failures, stack frames in *your* code, and summaries, folds the rest, and ends with a handle: `skimread show 42`, `skimread show 42 120-180`, `skimread show 42 --grep TypeError -C 3`. Nothing is ever more than one cheap call away, so the agent never has to re-run a build to see what it missed.

## Numbers

Measured on real tool output (fixtures in `tests/fixtures`, reproduce with `python bench/bench.py`). Token counts are estimates (chars / 3.6).

| Command output | lines | tokens in | tokens out | saved |
|---|---:|---:|---:|---:|
| pytest -v (1,504 tests, 2 failing) | 1546 | 33,876 | 473 | **99%** |
| vitest --reporter=verbose (579 tests, 3 failing) | 653 | 7,751 | 698 | **91%** |
| pytest (65 tests, 3 failing, 60 warnings) | 294 | 3,451 | 496 | **86%** |
| npm install (649 packages, 23 deprecation warnings) | 41 | 1,048 | 204 | **81%** |
| cargo build (37 crates, 3 warnings) | 61 | 559 | 254 | 55% |
| go test -v (1 fail, 1 panic) | 35 | 301 | 137 | 54% |
| tsc (48 errors in 8 files) | 48 | 1,087 | 628 | 42% |
| cargo test (1 fail, backtrace) | 68 | 713 | 487 | 32% |
| cargo build (2 errors) | 39 | 408 | 411 | -1% |
| vitest (13 files, 3 failing) | 76 | 613 | 628 | -2% |

The last two rows are the point: when the output is already pure signal (a short build with two errors), skimread leaves it alone. Savings come from noise, never from hiding failures.

| File read | lines | full read (tokens) | outline (tokens) | saved when the agent then reads a 60-line slice |
|---|---:|---:|---:|---:|
| skimread/outline.py | 598 | 7,648 | 477 | 84% |
| skimread/formats.py | 463 | 4,961 | 569 | 76% |
| skimread/cli.py | 368 | 4,086 | 357 | 75% |

A 4,063-line Go file (`net/http/server.go`, ~36k tokens) becomes a ~600-token map of structs and methods with line ranges. `skimread stats` shows what it has saved you so far, in tokens and dollars.

## Install

```bash
pipx install git+https://github.com/avikchakraborty060-create/The-top-innovation
# or: uv tool install git+https://github.com/avikchakraborty060-create/The-top-innovation
# or, inside any Python 3.9+ environment: pip install git+https://github.com/avikchakraborty060-create/The-top-innovation
```

No dependencies. Everything is stored under `~/.skimread` (owner-only permissions) and pruned after 7 days / 300 runs.

## Claude Code (one command)

```bash
skimread setup claude            # global: ~/.claude/settings.json
skimread setup claude --project  # or per project: .claude/settings.json
```

Restart Claude Code. From then on:

- A `Read` of a file over 400 lines (or 16 KB) without `offset`/`limit` is answered with an outline and the instruction to read a range. The same full read is allowed on the second attempt in the same session, so the agent is never stuck.
- `Bash` commands that are known to be noisy (`npm`, `pytest`, `cargo`, `go`, `tsc`, `docker`, `git diff`, `find`, …) are rewritten to run through `skimread run`, so Claude sees the digest and the handle. The permission rule `Bash(<path-to-skimread>:*)` is added so this does not cause extra prompts.

What the hook does to a big read:

```
skimread: src/server.py is 2,014 lines (~20k tokens). Read only the part you need: call Read again
with offset and limit chosen from this outline. Any Read that sets offset or limit is allowed; ...
Outline: 2,014 lines, ~20k tokens (python)
      1-38  imports 12 import statements
    41-118  class Config
    44-58     def __init__(self, path: str) -> None
    61-90     def load(self) -> dict
   121-410  class Server(Base)
   123-140    def __init__(self, cfg: Config)
   142-229    def handle_request(self, req: Request) -> Response
   ...
```

What Claude sees after `pytest -v` on a suite of 1,504 tests:

```
$ pytest -v test_many.py  ·  exit 1 in 1.2s
============================= test session starts ==============================
  (1502 passing/skipped tests hidden)
test_many.py::test_some_fail[1] FAILED                                   [ 99%]
test_many.py::test_some_fail[3] FAILED                                   [100%]
=================================== FAILURES ===================================
______________________________ test_some_fail[1] _______________________________
...
E       AssertionError: role mismatch for user 1
E       assert 'dev' == 'ops'
test_many.py:10: AssertionError
=========================== short test summary info ============================
FAILED test_many.py::test_some_fail[1] - AssertionError: role mismatch for user 1
FAILED test_many.py::test_some_fail[3] - AssertionError: role mismatch for user 3
======================== 2 failed, 1502 passed in 0.98s ========================
[skimread #42: 1546 lines, ~34k tok → 473. Full/slice/search: skimread show 42 [A-B | --grep RE]]
```

Options:

```bash
skimread setup claude --wrap all     # route every safe Bash command through skimread
skimread setup claude --wrap off     # Read guard only, leave Bash alone
skimread setup claude --uninstall
skimread config read_threshold_lines 600   # guard only files longer than this
skimread config budget 4000                # digest size in characters (default 6000)
skimread setup print                       # just print the settings.json snippet
```

The Bash wrapper never touches commands that change shell state (`cd` at the end, `export`, `source`), redirect to files, run in the background, look interactive, or are destructive/privileged (`rm`, `sudo`, `git push`, `git commit`, `curl | sh`, …). Those run exactly as Claude wrote them, under your normal permission rules. A leading `cd dir && …` is preserved outside the wrapper so the shell's working directory still changes.

## Any other agent

**CLI.** Tell the agent (in `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, a system prompt) to use it; `skimread setup agents` prints a ready-made snippet:

```bash
skimread outline src/server.py           # map of the file with line ranges
skimread read src/server.py 142-229      # numbered slice
skimread run -c "npm test"               # digest + handle   (also: skimread npm test)
skimread show 42                         # full stored output
skimread show 42 300-360                 # slice
skimread show 42 --grep "Error" -C 3     # search with context
skimread list                            # recent runs
skimread stats                           # tokens and dollars saved
```

**MCP.** `skimread mcp` is a stdio MCP server with tools `run`, `show`, `outline`, `read`, `list`. `skimread setup mcp` prints the config block for `mcpServers`.

**Pipe.** `cmd 2>&1 | skimread pipe --cmd "cmd"` digests anything on stdin.

## How it works

1. **Normalise.** ANSI colours and cursor codes are stripped, `\r` progress bars resolve to their final state, backspaces are applied.
2. **Format-aware reduction.** Reducers for pytest, jest/vitest/mocha, cargo, go, npm/yarn/pnpm/bun, pip/uv/poetry, docker, tsc, git diff, and file/listing commands drop only what carries no decision value: `PASSED` lines, `Compiling` lines, deprecation warnings (counted), stdlib stack frames. Everything else stays. Unknown tools get the generic path: progress noise removed, exact duplicates counted (`line   (x5)`), runs of near-identical lines folded (`… 847 similar lines (159-1005) …`).
3. **Scoring and budget.** Lines are scored (errors and stack frames high, summaries medium). If the reduced output still exceeds the budget, the head, the tail, and windows around the highest-scored lines are kept; every cut is replaced by `… [768 lines omitted (45-812) — skimread show 42 45-812] …`.
4. **Spool.** The full normalised output is written to `~/.skimread/out/<id>.txt` before the digest is produced, so the handle in the footer always works.
5. **Outline.** Python via the `ast` module (exact signatures and ranges); JS/TS, Go, Rust, Java/Kotlin/C#/Swift, C/C++, PHP, CSS via declaration regexes plus brace matching; Ruby/Elixir/Lua via indentation; Markdown headings; JSON/YAML/TOML keys; CSV shape; log files (time span, error line numbers); Makefiles, shell functions, SQL statements. Huge files fold members into their parents and truncate with a note rather than pretending to be complete.

## Honest limitations

- Token counts are estimates (chars / 3.6), not tokenizer output. Ratios are what matter and they are unaffected.
- Outlines for brace languages are regex-based. They are meant for navigation, not correctness; a weird formatting style can miss a symbol. Grep still works.
- The Read guard costs one extra tool round-trip for files just over the threshold. The default (400 lines) is chosen so the outline plus a slice is still much cheaper than the file; raise it if your agent mostly needs whole files.
- The Bash wrapper runs the command through `bash -c` in a child process. Shell functions and aliases defined in Claude Code's persistent shell are not visible to it (environment variables and the working directory are).
- Only Claude Code has a hook that can intercept the agent's native Read. Other agents get the same capability through the CLI/MCP tools plus an instruction in their rules file.

## Development

```bash
git clone https://github.com/avikchakraborty060-create/The-top-innovation && cd The-top-innovation
pip install -e ".[dev]"
python -m pytest -q          # 68 tests, < 1 s
python bench/bench.py        # the tables above
```

Add a format: write a `reduce_<tool>()` in `skimread/formats.py` that drops or folds noise lines and sets scores, register it in `detect()`, drop a real output sample into `tests/fixtures`, and add a row to `tests/test_formats.py` that states what must survive. Add a language: a pattern list in `skimread/outline.py`.

Contributions of real-world fixtures are the most valuable thing you can send: the reducers are only as good as the outputs they have seen.

## License

MIT.
