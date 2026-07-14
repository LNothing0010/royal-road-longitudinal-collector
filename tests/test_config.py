from pathlib import Path

import pytest

from rrlab.config import DEFAULT_USER_AGENT, RS_SOURCES, Settings
from rrlab.doctor import run_doctor


def test_settings_reads_environment_at_instantiation(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RR_USER_AGENT", "test-agent")
    first = Settings()
    monkeypatch.setenv("RR_USER_AGENT", "second-agent")
    second = Settings()
    assert first.user_agent == "test-agent"
    assert second.user_agent == "second-agent"


def test_empty_user_agent_uses_safe_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RR_USER_AGENT", "")
    assert Settings().user_agent == DEFAULT_USER_AGENT


def test_doctor_validates_contract(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "data" / "rrlab.sqlite",
        raw_dir=tmp_path / "raw",
        report_dir=tmp_path / "reports",
    )
    result = run_doctor(settings)
    assert result["ok"] is True
    assert len(RS_SOURCES) == 6
