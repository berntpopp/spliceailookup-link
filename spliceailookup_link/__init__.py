"""spliceailookup-link: MCP + REST server for SpliceAI / Pangolin splice prediction."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("spliceailookup-link")
except PackageNotFoundError:  # pragma: no cover - source tree without install
    __version__ = "0.0.0"
