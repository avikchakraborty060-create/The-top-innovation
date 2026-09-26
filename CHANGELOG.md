# Changelog

## 0.1.0

First release.

- Read guard for Claude Code: large `Read` calls without a range get a structural outline and a request to read a slice.
- Lossless command digests with stored full output and `show` handles (range, grep with context).
- Reducers for pytest, jest/vitest/mocha, cargo, go, npm/yarn/pnpm/bun, pip/uv/poetry, docker, tsc, git diff, file dumps and listings; generic path for unknown tools.
- Outlines for Python, JS/TS, Go, Rust, Java-likes, C/C++, PHP, CSS, Ruby-likes, Markdown, JSON/JSONL, YAML/TOML, CSV, logs, shell/Makefile, SQL, HTML.
- CLI, MCP stdio server, `setup claude` installer, `stats` in tokens and dollars.
