# Launch posts

Ready-to-paste text. Adjust the install line once the package is on PyPI (`pip install skimread`).

## Show HN

**Title:** Show HN: Skimread – a lossless context guard for AI coding agents

**Text:**

AI coding agents (Claude Code, Codex, Cursor) spend most of their tokens on two things they don't need: reading 2,000-line files to find one function, and reading 1,500 lines of `PASSED` to learn about 2 failures.

Skimread is a small, dependency-free Python tool that sits in between. For Claude Code it installs as a hook: when the agent is about to read a big file without a range, it gets an outline (functions, classes, headings, keys, each with line ranges) and reads only the slice it needs. Noisy commands run through it and come back as a digest: errors, failing tests, your stack frames, the summary line, and a handle (`skimread show 42 --grep TypeError`) to the full output, which is kept on disk.

The word that matters is lossless. Existing output compressors are lossy, and a JetBrains benchmark found that when the compressor drops the one line the agent needed, the agent re-runs the command and the net cost goes *up*. Skimread never drops anything unrecoverably, and it also covers file reads, which none of the compressors can touch.

Measured on real output: pytest -v with 1,504 tests goes from ~34k tokens to ~470 with both failures intact; a 4,000-line Go file becomes a ~600-token map. Short outputs that are already pure signal are left alone.

Zero deps, MIT, Python 3.9+. Works with any agent through a CLI and an MCP server.

Repo: https://github.com/avikchakraborty060-create/The-top-innovation

## Reddit (r/ClaudeAI, r/ChatGPTCoding, r/LocalLLaMA)

**Title:** I built a hook that stops Claude Code from reading whole files and full test logs. Lossless, zero deps, one command to install.

**Body:**

Every Claude Code session I watched did the same two expensive things: `Read` a huge file to find one function, and run `npm test` or `pytest` and pull thousands of lines of passing tests into context.

So I built **skimread**. `pipx install` it, run `skimread setup claude`, restart Claude Code, done.

- Big `Read` → Claude gets an outline with line ranges and reads just the slice.
- Noisy `Bash` (tests, builds, installs, diffs) → Claude gets a digest: failures, errors, your stack frames, summary. Full output stays on disk; `skimread show <id>` gets any part of it back.
- `skimread stats` shows tokens and dollars saved.

Numbers from real runs: pytest -v with 1,504 tests: 33,876 tokens → 473. vitest verbose: 7,751 → 698. npm install with 649 packages: 1,048 → 204. Short outputs with only errors in them are left untouched, which is the point: it saves on noise, never on signal.

Also works with Codex/Cursor/anything through the CLI or as an MCP server.

Repo: https://github.com/avikchakraborty060-create/The-top-innovation

## X / Bluesky / LinkedIn (short)

Your coding agent reads 2,000 lines to find one function and 1,500 PASSED lines to find 2 failures. skimread fixes both, losslessly: outlines instead of whole files, digests instead of logs, full output always one call away. Zero deps, one command for Claude Code, MCP for the rest. pytest -v: 34k tokens → 473. https://github.com/avikchakraborty060-create/The-top-innovation

## Where to post

- Hacker News (Show HN), best Tuesday to Thursday, 8-10am US Eastern.
- Reddit: r/ClaudeAI, r/ChatGPTCoding, r/LocalLLaMA, r/Python.
- Claude Developers Discord, "share your projects".
- MCP directories: registry.modelcontextprotocol.io (uses `server.json` in this repo), mcp.so, smithery.ai, pulsemcp.com.
- Awesome lists (open a PR adding one line): awesome-claude-code, awesome-mcp-servers.
