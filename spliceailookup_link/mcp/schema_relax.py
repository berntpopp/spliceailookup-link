"""Loosen JSON Schemas so success-shaped output schemas don't reject error envelopes.

The MCP SDK validates every tool response against the declared output schema. When
a tool returns an error envelope (e.g. {success: false, error_code: "not_found"}),
strict success schemas with required fields reject it and the SDK replaces the
payload with an opaque "Output validation error". Stripping `required` keeps the
schema's LLM-discovery value while letting error envelopes flow through unchanged.
"""

from __future__ import annotations

from typing import Any

_SCALAR_TYPES = frozenset({"integer", "number", "string", "boolean"})


def relax_output_schema(schema: Any) -> Any:
    """Return a deep-copied schema with `required` stripped and additionalProperties=True.

    Recurses into `properties`, `items`, `$defs`, `definitions`, `oneOf`, `anyOf`,
    `allOf`. Non-dict inputs are returned unchanged.
    """
    if not isinstance(schema, dict):
        return schema

    relaxed: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "required":
            continue
        if key == "additionalProperties":
            relaxed[key] = True
            continue
        if key == "properties" and isinstance(value, dict):
            relaxed[key] = {k: relax_output_schema(v) for k, v in value.items()}
            continue
        if key == "items":
            if isinstance(value, list):
                relaxed[key] = [relax_output_schema(v) for v in value]
            else:
                relaxed[key] = relax_output_schema(value)
            continue
        if key in ("$defs", "definitions") and isinstance(value, dict):
            relaxed[key] = {k: relax_output_schema(v) for k, v in value.items()}
            continue
        if key in ("oneOf", "anyOf", "allOf") and isinstance(value, list):
            relaxed[key] = [relax_output_schema(v) for v in value]
            continue
        relaxed[key] = value

    if relaxed.get("type") == "object" and "additionalProperties" not in relaxed:
        relaxed["additionalProperties"] = True

    if "enum" not in relaxed and "const" not in relaxed:
        type_value = relaxed.get("type")
        if isinstance(type_value, str) and type_value in _SCALAR_TYPES:
            relaxed["type"] = [type_value, "null"]
        elif (
            isinstance(type_value, list)
            and type_value
            and "null" not in type_value
            and all(token in _SCALAR_TYPES for token in type_value)
        ):
            relaxed["type"] = [*type_value, "null"]

    return relaxed
