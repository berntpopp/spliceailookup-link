"""Per-call telemetry for scoring (cache hit/miss + upstream timing + warmth).

Hit/miss is decided at the score() boundary by membership in the service's
set of already-computed keys, checked BEFORE the await. This is concurrency
safe (distinct keys are independent; async_lru runs leaves in their own tasks,
so a ContextVar set inside a leaf would not propagate back) and best-effort
after TTL expiry / LRU eviction, which is acceptable for advisory telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass

from spliceailookup_link.config import settings


def is_served_warm(
    cache: str | None, upstream_elapsed_ms: int | None, threshold_ms: int | None = None
) -> bool:
    """True when the response avoided a cold start: a cache hit, or an upstream
    answer faster than threshold_ms. Unknown timing on a non-hit is not warm."""
    if threshold_ms is None:
        threshold_ms = settings.WARM_THRESHOLD_MS
    if cache == "hit":
        return True
    if upstream_elapsed_ms is not None:
        return upstream_elapsed_ms < threshold_ms
    return False


@dataclass(slots=True)
class CallTelemetry:
    cache: str  # "hit" | "miss"
    upstream_elapsed_ms: int | None = None
    cache_age_s: int | None = None
    cache_ttl_s: int | None = None

    def served_warm(self, threshold_ms: int | None = None) -> bool:
        return is_served_warm(self.cache, self.upstream_elapsed_ms, threshold_ms)
