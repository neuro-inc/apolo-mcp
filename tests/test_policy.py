import pytest

from apolo_mcp.ledger import Ledger
from apolo_mcp.policy import (
    POLICY_MODE_ENV,
    MutationEffect,
    Policy,
    PolicyMode,
    _reset_policy_for_tests,
    current_policy,
)


def test_read_only_is_default_and_denies_every_mutation(monkeypatch) -> None:
    monkeypatch.delenv(POLICY_MODE_ENV, raising=False)
    policy = Policy.load()
    assert policy.mode is PolicyMode.READ_ONLY
    with pytest.raises(PermissionError, match="read-only server policy"):
        policy.authorize(operation="create_disk", effect=MutationEffect.CREATE)


def test_removed_high_risk_switch_does_not_enable_mutations(monkeypatch) -> None:
    monkeypatch.delenv(POLICY_MODE_ENV, raising=False)
    monkeypatch.setenv("APOLO_MCP_ENABLE_HIGH_RISK", "true")
    assert Policy.load().mode is PolicyMode.READ_ONLY


def test_environment_policy_is_frozen_for_process(monkeypatch) -> None:
    monkeypatch.setenv(POLICY_MODE_ENV, "managed")
    assert current_policy().mode is PolicyMode.MANAGED
    monkeypatch.setenv(POLICY_MODE_ENV, "full")
    assert current_policy().mode is PolicyMode.MANAGED

    _reset_policy_for_tests()
    assert current_policy().mode is PolicyMode.FULL


def test_invalid_environment_policy_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv(POLICY_MODE_ENV, "perhaps")
    with pytest.raises(ValueError, match="must be one of"):
        Policy.load()


def test_managed_allows_create_but_requires_exact_active_lifecycle(
    tmp_path, monkeypatch
) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("APOLO_MCP_LEDGER_PATH", str(ledger_path))
    policy = Policy(PolicyMode.MANAGED)
    assert policy.authorize(operation="run_job", effect=MutationEffect.CREATE) is None
    exact = {
        "resource_type": "job",
        "resource_id": "job-1",
        "username": "user@example.test",
        "cluster": "alpha",
        "org": "team",
        "project": "default",
    }
    with pytest.raises(PermissionError, match="no active creation lifecycle"):
        policy.authorize(operation="kill_job", effect=MutationEffect.DELETE, **exact)
    Ledger().append(operation="run_job", action="created", **exact)
    assert policy.authorize(operation="kill_job", effect=MutationEffect.DELETE, **exact)
    with pytest.raises(PermissionError, match="no active creation lifecycle"):
        policy.authorize(
            operation="kill_job",
            effect=MutationEffect.DELETE,
            **{**exact, "project": "other"},
        )
    with pytest.raises(PermissionError, match="no active creation lifecycle"):
        policy.authorize(
            operation="kill_job",
            effect=MutationEffect.DELETE,
            **{**exact, "username": "another-user@example.test"},
        )


def test_full_allows_all_mutations_without_journal() -> None:
    policy = Policy(PolicyMode.FULL)
    assert (
        policy.authorize(operation="delete_disk", effect=MutationEffect.DELETE) is None
    )
