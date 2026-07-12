"""F-19: the builder must not bootstrap a floating pip/uv.

The image builder previously ran ``pip install --upgrade pip uv`` -- an
unbounded, non-reproducible install. Replace it with a digest-pinned uv binary
copied from the official image so the build is reproducible. Research use only;
not clinical decision support.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # tests/unit/ -> repo root
DOCKERFILE = ROOT / "docker" / "Dockerfile"

_UV_PIN = (
    "ghcr.io/astral-sh/uv:0.8.7@sha256:"
    "1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab"
)


def test_dockerfile_has_no_floating_pip_upgrade() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "pip install --upgrade" not in text, (
        "floating pip/uv upgrade must be removed (non-reproducible bootstrap)"
    )


def test_dockerfile_pins_uv_by_digest() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert f"COPY --from={_UV_PIN} /uv /usr/local/bin/uv" in text, (
        "uv must be copied from the digest-pinned official image"
    )
