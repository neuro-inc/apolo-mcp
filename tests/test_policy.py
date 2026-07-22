import json

import pytest

from apolo_mcp.policy import HIGH_RISK_ENV, POLICY_FILE_ENV, Policy


def test_high_risk_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv(HIGH_RISK_ENV, raising=False)
    monkeypatch.delenv(POLICY_FILE_ENV, raising=False)
    policy = Policy.load()
    with pytest.raises(PermissionError, match="disabled by server policy"):
        policy.require_high_risk("create_service_account")


def test_policy_file_and_environment_override(tmp_path, monkeypatch) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"enable_high_risk": True}), encoding="utf-8")
    monkeypatch.setenv(POLICY_FILE_ENV, str(path))
    monkeypatch.delenv(HIGH_RISK_ENV, raising=False)
    assert Policy.load().enable_high_risk is True
    monkeypatch.setenv(HIGH_RISK_ENV, "false")
    assert Policy.load().enable_high_risk is False


def test_invalid_environment_policy_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv(POLICY_FILE_ENV, raising=False)
    monkeypatch.setenv(HIGH_RISK_ENV, "perhaps")
    with pytest.raises(ValueError, match="must be a boolean"):
        Policy.load()
