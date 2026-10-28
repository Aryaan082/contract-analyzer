"""The parked CI workflow (.github/workflows-disabled/evals.yml): one workflow, nothing switched on,
no secret, only the free subset, held-out by hand. Reads files; touches no network."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


# -- the parked workflow ------------------------------------------------------------------------


WORKFLOW = ROOT / ".github" / "workflows-disabled" / "evals.yml"


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def test_there_is_one_parked_evals_workflow_and_nothing_is_switched_on():
    assert WORKFLOW.exists()
    assert not (ROOT / ".github" / "workflows-disabled" / "retrieval.yml").exists()
    assert not (ROOT / ".github" / "workflows").exists()


def test_the_workflow_has_the_jobs_the_plan_calls_for_and_the_wider_paths():
    spec = workflow()
    assert set(spec["jobs"]) == {"tests", "retrieval", "stored-results", "held-out"}
    paths = spec[True]["pull_request"]["paths"]  # YAML reads the key `on` as true
    for wanted in ("agent/**", "chain/**", "references/**", "evals/**", "tests/**", "retrieval/**"):
        assert wanted in paths


def test_held_out_runs_only_when_started_by_hand_and_never_on_a_pull_request():
    spec = workflow()
    assert (
        spec["jobs"]["held-out"]["if"]
        == "github.event_name == 'workflow_dispatch' && inputs.held_out"
    )
    assert (
        "workflow_dispatch" in spec[True]
        and "held_out" in spec[True]["workflow_dispatch"]["inputs"]
    )
    assert not any(
        "--held-out" in step.get("run", "")
        for name, job in spec["jobs"].items()
        if name != "held-out"
        for step in job["steps"]
    )


def test_the_workflow_uses_no_secret_and_no_paid_or_network_command():
    text = WORKFLOW.read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "secrets." not in code and "env:" not in code
    for name in ("OPENROUTER", "ALCHEMY", "ETHERSCAN", "RPC_URL"):
        assert name not in code
    assert not re.search(r"evals\.run (generate|heldout)|evals\.judge|--go\b|make eval-run", code)
    assert "permissions:" in code and "contents: read" in code


def test_timing_is_reported_and_never_gated():
    spec = workflow()
    runs = [s.get("run", "") for s in spec["jobs"]["stored-results"]["steps"]]
    assert "uv run python -m evals.run check-summaries" in runs
    text = WORKFLOW.read_text()
    assert "never gated" in text.lower() or "never gate" in text.lower()


def test_the_header_says_what_ci_does_not_do_and_lists_the_steps_before_enabling():
    header = "\n".join(line for line in WORKFLOW.read_text().splitlines() if line.startswith("#"))
    flat = " ".join(header.replace("#", " ").split())
    assert "does NOT run the agent" in flat and "runs in CI on every PR" in flat
    assert "git mv .github/workflows-disabled/evals.yml .github/workflows/evals.yml" in flat
    for step in (
        "Re-check the action versions",
        "Re-save the retrieval baseline",
        "GitHub Actions minutes",
        "Rotate the Alchemy key",
    ):
        assert step in flat
    assert "NOT re-checked since" in flat
