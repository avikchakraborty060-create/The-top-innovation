# skimread — skim before you read

**A lossless context guard for AI coding agents.** Zero dependencies, runs entirely on your machine, MIT licensed. Works with Claude Code out of the box, and with any other agent (Codex, Cursor, Aider, Copilot, your own) through a CLI and an MCP server.

## What is it?

When you use an AI coding agent, you pay for every token it reads. Most of that money goes to two things the agent does not actually need:

1. **Reading big files whole.** The agent wants one function and reads all 2,000 lines of the file to find it. Next session it does it again.
2. **Reading noisy command output.** `pytest -v` prints 1,500 `PASSED` lines to tell you about 2 failures. `npm install` prints 40 deprecation warnings. `cargo build` lists every crate it compiled.

skimread sits between the agent and those two sources and hands the agent a *skim* first:

- **For files: an outline instead of the file.** When the agent is about to read a large file, skimread gives it a map of the file (functions, classes, headings, keys, each with its line range) and says "read only the slice you need". The agent then reads 60 lines instead of 2,000.
- **For commands: a digest instead of the dump.** Noisy commands run through skimread. The agent sees the errors, the failing tests, the stack frames in *your* code, and the summary line. The 1,500 `PASSED` lines are folded into one line that says so.

The key word is **lossless**. Nothing is thrown away. Every command's full output is kept on your disk, and every digest ends with a handle (`skimread show 42`, `skimread show 42 120-180`, `skimread show 42 --grep TypeError`). Every outline tells the agent exactly which line range to read next. The agent is never stuck and never has to re-run a build to see what it missed.

### Why not one of the existing output compressors?

Tools that compress shell output already exist (RTK, token-saver, condense). They share two limits. They cannot touch the agent's own file-reading tool, which is the bigger token sink. And they are lossy: when the compressor drops the one line the agent needed, the agent re-runs the command, and an [independent benchmark](https://blog.jetbrains.com/ai/2026/07/rtk-claude-code-token-savings/) found that this can cost *more* than not compressing at all. skimread guards file reads too, and keeps everything retrievable.

### What it is not

- Not a cloud service. No network calls, no accounts, no telemetry. `~/.skimread` is the whole state.
- Not an LLM. Everything is deterministic parsing and folding, so it runs in milliseconds and costs nothing.
- Not a summariser that guesses. It never rewrites a line; it only hides lines it can name and count, and tells you how to get them back.

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

## How to use

### 1. Install (30 seconds)

Needs Python 3.9 or newer, nothing else.

```bash
pipx install git+https://github.com/avikchakraborty060-create/The-top-innovation
# or: uv tool install git+https://github.com/avikchakraborty060-create/The-top-innovation
# or, inside any Python environment: pip install git+https://github.com/avikchakraborty060-create/The-top-innovation
skimread --version
```

Everything skimread stores lives under `~/.skimread` with owner-only permissions and is pruned after 7 days or 300 runs.

### 2. Claude Code: one command

```bash
skimread setup claude            # global: ~/.claude/settings.json
skimread setup claude --project  # or per project: .claude/settings.json
```

Restart Claude Code. That is all. From then on:

- When Claude tries to `Read` a file over 400 lines (or 16 KB) without a line range, it gets the outline and the instruction to read a range instead. A ranged read is always allowed. If Claude asks for the same whole file a second time in the session, it gets it, so it is never stuck.
- When Claude runs a command known to be noisy (`npm`, `pytest`, `cargo`, `go`, `tsc`, `docker`, `git diff`, `find`, and about 100 others) the command is rewritten to run through `skimread run`. Claude sees the digest and the handle. The permission rule `Bash(<path-to-skimread>:*)` is added so this causes no extra prompts.

This is what Claude sees instead of a 2,014-line file:

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

And this is what Claude sees after `pytest -v` on a suite of 1,504 tests:

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

Tuning:

```bash
skimread setup claude --wrap all     # route every safe Bash command through skimread
skimread setup claude --wrap off     # Read guard only, leave Bash alone
skimread setup claude --uninstall    # remove the hook and the permission rule
skimread config read_threshold_lines 600   # guard only files longer than this (default 400)
skimread config budget 4000                # digest size in characters (default 6000)
skimread setup print                       # just print the settings.json snippet to paste yourself
```

Safety: the Bash wrapper never touches commands that change shell state (`cd` at the end, `export`, `source`), redirect to files, run in the background, look interactive, or are destructive or privileged (`rm`, `sudo`, `git push`, `git commit`, `curl | sh`, and so on). Those run exactly as Claude wrote them, under your normal permission rules. A leading `cd dir && …` is kept outside the wrapper so the working directory still changes.

### 3. Codex, Cursor, Aider, Copilot, or your own agent

Any agent that can run shell commands can use skimread through the CLI. Put this in the agent's rules file (`AGENTS.md`, `.cursorrules`, a system prompt); `skimread setup agents` prints a ready-made snippet:

```bash
skimread outline src/server.py           # map of the file with line ranges
skimread read src/server.py 142-229      # numbered slice
skimread run -c "npm test"               # digest + handle   (shorthand: skimread npm test)
skimread show 42                         # full stored output
skimread show 42 300-360                 # slice
skimread show 42 --grep "Error" -C 3     # search with context
skimread list                            # recent runs
```

Agents that speak MCP can use it as a server instead. `skimread mcp` is a stdio MCP server with tools `run`, `show`, `outline`, `read`, `list`; `skimread setup mcp` prints the config block for `mcpServers`. Anything on stdin works too: `cmd 2>&1 | skimread pipe --cmd "cmd"`.

### 4. See what it saved you

```bash
skimread stats                 # tokens and dollars saved, all time, by format
skimread stats --price 15      # use your model's input price per million tokens
skimread stats --json
```

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
