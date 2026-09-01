"""GeneFoundry deploy contract: the deployed npm overlay must declare a numeric
non-root user for every service; the release Compose files (container-release.json)
must NOT declare `user`, since the shared release gate forbids that key there."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]  # tests/unit/ -> repo root

USER_RE = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")


class _TagTolerantLoader(yaml.SafeLoader):
    """SafeLoader that tolerates custom Compose tags like !reset / !override."""


_TagTolerantLoader.add_multi_constructor(
    "!",
    lambda loader, _suffix, node: (
        loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else None
    ),
)


def _load_compose(path: Path) -> dict:
    # _TagTolerantLoader subclasses yaml.SafeLoader (no arbitrary object
    # construction); ruff's S506 cannot see that from the loader argument alone.
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_TagTolerantLoader)  # noqa: S506


def test_npm_overlay_declares_numeric_user_for_every_service() -> None:
    compose = _load_compose(ROOT / "docker" / "docker-compose.npm.yml")
    services = compose["services"]
    assert services, "docker-compose.npm.yml should declare at least one service"
    for name, svc in services.items():
        user = svc.get("user")
        assert user is not None, f"{name} does not declare `user` in the npm overlay"
        assert USER_RE.match(str(user)), (
            f"{name} declares user={user!r}; the fleet controller requires a "
            "numeric non-root uid:gid (e.g. '999:999')"
        )


def test_release_compose_files_do_not_declare_user() -> None:
    release_config = json.loads((ROOT / "container-release.json").read_text(encoding="utf-8"))
    compose_files = release_config["service"]["compose_files"]
    assert compose_files, "container-release.json should list at least one compose file"
    for rel_path in compose_files:
        compose = _load_compose(ROOT / rel_path)
        for name, svc in compose["services"].items():
            assert "user" not in svc, (
                f"{name} in {rel_path} declares `user`; the release gate "
                "(container_release.py validate-compose / ALLOWED_SERVICE_KEYS) "
                "forbids `user` in release Compose files"
            )
