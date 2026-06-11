# CLAUDE.md

@AGENTS.md

Claude Code entrypoint only:

- Use `AGENTS.md` for shared repository instructions.
- Keep Claude-specific additions here short and tool-specific.
- Prefer `make ci-local` before final handoff. It runs `lint-loc`, which
  enforces the 600-LOC per-file budget.
- When planning an edit that would push a `spliceailookup_link/` module past
  about 500 lines, propose a cohesive split first rather than growing the file.
- Remember: the upstream scoring API reports errors as HTTP 200 + an `error`
  field, and calls can take 30s+. Do not "fix" a slow/empty response by
  shortening timeouts or treating HTTP 200 as unconditional success.
