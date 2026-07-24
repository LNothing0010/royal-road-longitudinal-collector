from pathlib import Path


def test_hourly_workflow_persists_step_outcomes_and_truthful_commit_message():
    workflow = Path(".github/workflows/hourly.yml").read_text(encoding="utf-8")

    assert "id: collect_panel" in workflow
    assert "reports/hourly_attempt_latest.json" in workflow
    assert '"collection_succeeded"' in workflow
    assert '"panel_validation_succeeded"' in workflow
    assert "steps.collect_panel.outcome" in workflow
    assert "Record failed Royal Road collection attempt" in workflow
    assert "RR_COLLECTION_SUCCEEDED" in workflow


def test_stale_reports_are_not_presented_as_a_successful_current_run():
    workflow = Path(".github/workflows/hourly.yml").read_text(encoding="utf-8")

    success_guard = (
        'cadence.get("should_collect") and attempt.get("collection_succeeded")'
    )
    assert success_guard in workflow
    assert "Collection was due but did not complete" in workflow
