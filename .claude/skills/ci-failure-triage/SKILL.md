---
name: ci-failure-triage
description: Use when a CI check (lint, format, mypy, line-budget, or tests) fails in spliceailookup-link
---

# Triaging a CI failure

1. **Reproduce locally** with the same command CI runs: `make ci-local` (or the
   specific failing target: `make lint`, `make format-check`, `make lint-loc`,
   `make typecheck`, `make test-fast`).
2. **Classify** the failure:
   - format/lint -> `make format` then `make lint-fix`; re-run.
   - line-budget -> a module exceeded 600 lines; split it cohesively (see AGENTS.md).
   - mypy -> fix the type, do not broaden ignores in `pyproject.toml`.
   - unit test -> a deterministic regression; fix the code or the test, never `xfail` to hide it.
   - integration test -> may be a live-upstream flake (rate limit / 503 / cold start).
     Integration tests are excluded from default CI; confirm before treating as a code bug.
3. **Distinguish flake from bug**: the SpliceAI/Pangolin upstream is rate-limited and can
   503. A `rate_limited` / `upstream_unavailable` envelope in an integration test is expected
   under load, not a code defect. Re-run with backoff before changing production code.
4. **Verify** the fix with `make ci-local` before pushing.
