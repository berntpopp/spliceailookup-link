"""F-18: the repo must ship a CodeQL + BLOCKING dependency-review workflow.

CodeQL and dependency-review were absent for this repo. The workflow must parse
as valid YAML, run CodeQL on pull requests, and gate the PR on a high-severity
dependency finding (no ``continue-on-error``, explicit ``fail-on-severity:
high``). Every action must be pinned to a 40-char commit SHA.

Research use only; not clinical decision support.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]  # tests/unit/ -> repo root
WORKFLOW = ROOT / ".github" / "workflows" / "security.yml"


def _load() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_security_workflow_exists_and_parses() -> None:
    assert WORKFLOW.exists(), "security.yml (CodeQL + dependency-review) must exist"
    doc = _load()
    jobs = doc["jobs"]
    assert "codeql" in jobs, "a CodeQL job is required"
    assert "dependency-review" in jobs, "a dependency-review job is required"


def test_runs_on_pull_requests() -> None:
    doc = _load()
    # PyYAML maps the bare `on:` key to the boolean True.
    triggers = doc.get("on", doc.get(True))
    assert "pull_request" in triggers, "CodeQL/dependency-review must run on pull requests"


def test_dependency_review_is_blocking_high_severity() -> None:
    doc = _load()
    steps = doc["jobs"]["dependency-review"]["steps"]
    review = [s for s in steps if "dependency-review-action" in str(s.get("uses", ""))]
    assert review, "dependency-review job must use actions/dependency-review-action"
    step = review[0]
    assert "continue-on-error" not in step, (
        "dependency-review must be BLOCKING: remove continue-on-error"
    )
    assert step.get("with", {}).get("fail-on-severity") == "high", (
        "dependency-review must set fail-on-severity: high"
    )


def test_every_action_is_sha_pinned() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    uses = re.findall(r"uses:\s*(\S+)", text)
    assert uses, "workflow should reference at least one action"
    for ref in uses:
        assert re.search(r"@[0-9a-f]{40}$", ref), f"action not SHA-pinned: {ref}"
