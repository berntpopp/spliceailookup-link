# Changelog

All notable changes to `spliceailookup-link` are documented here. This project
adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-06-15

### Breaking — GeneFoundry Tool-Naming Standard v1 (closes #2)

Adopts the fleet-wide **GeneFoundry Tool-Naming & Normalization Standard v1** so
this server composes cleanly behind the `genefoundry-router` gateway (mounted under
the namespace token **`spliceai`**; tools surface as `spliceai_<tool>`). No
deprecation aliases — the old argument names are removed immediately.

**Argument renames (fleet canon, Rule 4):**

- `variant` → `variant_id` on `predict_splicing`, `predict_spliceai`,
  `predict_pangolin`, and `resolve_variant`.
- `variants` → `variant_ids` on `predict_splicing_batch`.

`_meta.next_commands` and error `fallback_args` hints now emit the canonical
`variant_id` / `variant_ids` keys so they remain directly callable.

**`response_mode` enum:** now the fleet ladder `minimal | compact | standard |
full` (added `standard`, which behaves as `compact` in this server). `compact`
remains the default.

**Migration:** rename the argument in every call —
`predict_splicing(variant=...)` → `predict_splicing(variant_id=...)`, and
`predict_splicing_batch(variants=[...])` → `predict_splicing_batch(variant_ids=[...])`.
Tool names are unchanged (`predict_*`, `resolve_variant`,
`get_server_capabilities`, `warmup`).

### Notes

- Tool names were already unprefixed `verb_noun` snake_case and within the length
  budget. Per issue #2 (resolution A), `predict` is part of this server's
  canonical verb set (ML splice-score inference); the `ops`-tagged `warmup`
  utility is exempt from the verb rule.
- `serverInfo.name` remains the stable identity `spliceailookup-link`; the gateway
  namespace token `spliceai` is now documented in the README (Rule 5).
- CI guard added: `tests/unit/test_tool_names.py` asserts every registered tool
  name matches `^[a-z0-9_]{1,50}$`, starts with a canonical verb (or is
  `ops`-tagged), does not self-prefix `spliceai_`, and that the variant argument
  and `response_mode` enum follow the fleet canon (Rule 8).
