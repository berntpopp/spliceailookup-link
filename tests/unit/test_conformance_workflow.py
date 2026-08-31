"""Regression coverage for the live MCP conformance workflow."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "conformance.yml"


def test_failure_logs_do_not_follow_a_live_container() -> None:
    """A failed probe must emit logs then reach teardown instead of hanging CI."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["conformance"]["steps"]
    logs = next(step for step in steps if step.get("name") == "Logs on failure")

    assert "docker-logs" not in logs["run"]
    assert "logs --no-color" in logs["run"]
