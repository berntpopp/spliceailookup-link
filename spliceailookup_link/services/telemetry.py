"""Per-call telemetry for scoring (cache hit/miss + upstream timing).

Hit/miss is decided at the score() boundary by membership in the service's
set of already-computed keys, checked BEFORE the await. This is concurrency
safe (distinct keys are independent; async_lru runs leaves in their own tasks,
so a ContextVar set inside a leaf would not propagate back) and best-effort
after TTL expiry / LRU eviction, which is acceptable for advisory telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CallTelemetry:
    cache: str  # "hit" | "miss"
    upstream_elapsed_ms: int | None = None
