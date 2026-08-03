# mypy: disable-error-code="no-untyped-def"

import json
import stat
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import apolo_sdk
import pytest
from mcp.server import MCPServer

from apolo_mcp._client import reset_client_provider, set_client_provider
from apolo_mcp.ledger import Ledger, redact_credentials
from apolo_mcp.tools.jobs import register


def append_job(ledger: Ledger, resource_id: str, **overrides):
    values: dict[str, Any] = {
        "resource_type": "job",
        "resource_id": resource_id,
        "username": "user@example.test",
        "cluster": "alpha",
        "org": "team",
        "project": "default",
        "operation": "run_job",
        "action": "created",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return ledger.append(**values)


def test_exact_ownership_never_infers_from_name(tmp_path):
    ledger = Ledger(tmp_path / "private" / "ledger.jsonl")
    owned = append_job(ledger, "job-exact")

    assert (
        ledger.lookup(
            resource_type="job",
            resource_id="job-exact",
            username="user@example.test",
            cluster="alpha",
            org="team",
            project="default",
        )
        == owned
    )
    assert (
        ledger.authorize_owned_resource(
            resource_type="job",
            resource_id="job-exact",
            username="user@example.test",
            cluster="alpha",
            org="team",
            project="default",
        )
        == owned
    )
    with pytest.raises(PermissionError, match="no active creation lifecycle"):
        ledger.authorize_owned_resource(
            resource_type="job",
            resource_id="job-exact-copy",
            username="user@example.test",
            cluster="alpha",
            org="team",
            project="default",
        )


@pytest.mark.parametrize(
    "field", ["resource_type", "username", "cluster", "org", "project"]
)
def test_lookup_rejects_type_or_context_mismatch(tmp_path, field):
    ledger = Ledger(tmp_path / "private" / "ledger.jsonl")
    append_job(ledger, "job-1")
    query = {
        "resource_type": "job",
        "resource_id": "job-1",
        "username": "user@example.test",
        "cluster": "alpha",
        "org": "team",
        "project": "default",
    }
    query[field] = "different"
    assert ledger.lookup(**query) is None


def test_append_only_lifecycle_requires_creation_after_latest_delete(tmp_path):
    ledger = Ledger(tmp_path / "private" / "ledger.jsonl")
    exact = {
        "resource_type": "job",
        "resource_id": "job-1",
        "username": "user@example.test",
        "cluster": "alpha",
        "org": "team",
        "project": "default",
    }
    ledger.append(operation="run_job", action="created", **exact)
    ledger.append(operation="bump_job_life_span", action="updated", **exact)
    assert ledger.authorize_owned_resource(**exact).action == "updated"

    ledger.append(operation="delete_job", action="deleted", **exact)
    with pytest.raises(PermissionError, match="no active creation lifecycle"):
        ledger.authorize_owned_resource(**exact)

    ledger.append(operation="run_job", action="created", **exact)
    assert [entry.action for entry in ledger.history(**exact)] == [
        "created",
        "updated",
        "deleted",
        "created",
    ]
    assert ledger.authorize_owned_resource(**exact).action == "created"


def test_update_record_alone_never_establishes_managed_ownership(tmp_path):
    ledger = Ledger(tmp_path / "private" / "ledger.jsonl")
    exact = {
        "resource_type": "app",
        "resource_id": "app-existing",
        "username": "user@example.test",
        "cluster": "alpha",
        "org": "team",
        "project": "default",
    }
    ledger.append(operation="configure_app", action="updated", **exact)
    with pytest.raises(PermissionError, match="no active creation lifecycle"):
        ledger.authorize_owned_resource(**exact)


def test_unknown_ledger_shape_still_fails_closed(tmp_path):
    path = tmp_path / "private" / "ledger.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"unexpected":"shape"}\n')

    with pytest.raises(ValueError, match="unexpected or missing fields"):
        Ledger(path).entries()


def test_concurrent_append_is_valid_jsonl_with_private_permissions(tmp_path):
    path = tmp_path / "private" / "ledger.jsonl"
    ledger = Ledger(path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: append_job(ledger, f"job-{index}"), range(100)))

    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert {record["resource_id"] for record in records} == {
        f"job-{index}" for index in range(100)
    }
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_credentials_are_rejected_without_leaking_and_redaction_is_available(tmp_path):
    ledger = Ledger(tmp_path / "private" / "ledger.jsonl")
    credential = "token=super-sensitive-value"
    with pytest.raises(ValueError) as raised:
        append_job(ledger, credential)
    assert "super-sensitive-value" not in str(raised.value)
    assert redact_credentials(f"failed: {credential}") == "failed: <redacted>"
    assert not ledger.path.exists()


class Provider:
    def __init__(self, sdk):
        self.sdk = sdk

    @asynccontextmanager
    async def client(self):
        yield self.sdk


async def test_run_job_records_exact_created_id_and_resolved_context(
    tmp_path, monkeypatch
):
    path = tmp_path / "private" / "ledger.jsonl"
    monkeypatch.setenv("APOLO_MCP_LEDGER_PATH", str(path))
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "full")
    history = SimpleNamespace(
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        started_at=None,
        finished_at=None,
        exit_code=None,
        reason="",
    )
    started = SimpleNamespace(
        id="sdk-generated-id",
        name="friendly-name",
        owner="alice",
        status=apolo_sdk.JobStatus.PENDING,
        container=SimpleNamespace(
            image="ubuntu:24.04", entrypoint=None, command=None, working_dir=None
        ),
        preset_name="cpu-small",
        scheduler_enabled=False,
        restart_policy=apolo_sdk.JobRestartPolicy.NEVER,
        life_span=None,
        schedule_timeout=None,
        energy_schedule_name=None,
        priority=None,
        history=history,
        description=None,
        tags=(),
    )

    class Jobs:
        async def start(self, **kwargs):
            assert path.exists()
            assert path.read_text() == ""
            return started

    config = SimpleNamespace(
        username="user@example.test",
        cluster_name="alpha",
        org_name="team",
        project_name="default",
        clusters={
            "alpha": SimpleNamespace(
                orgs=["team"],
                presets={"cpu-small": SimpleNamespace(scheduler_enabled=False)},
            )
        },
        projects={
            "default": SimpleNamespace(
                cluster_name="alpha", org_name="team", name="default"
            )
        },
    )
    sdk = SimpleNamespace(
        config=config,
        jobs=Jobs(),
        parse=SimpleNamespace(remote_image=lambda value, **kwargs: value),
    )
    token = set_client_provider(Provider(sdk))
    mcp = MCPServer("ledger-job-test")
    register(mcp)
    try:
        result = await mcp._tool_manager._tools["run_job"].fn(
            "ubuntu:24.04", "cpu-small"
        )
    finally:
        reset_client_provider(token)

    entry = Ledger(path).entries()[0]
    assert entry.resource_id == "sdk-generated-id"
    assert (entry.cluster, entry.org, entry.project) == ("alpha", "team", "default")
    assert entry.operation == "run_job"
    assert entry.action == "created"
    assert result["job"]["name"] == "friendly-name"


async def test_run_job_preflight_rejects_symlink_before_sdk_creation(
    tmp_path, monkeypatch
):
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    monkeypatch.setenv("APOLO_MCP_LEDGER_PATH", str(linked_parent / "ledger.jsonl"))
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "full")

    class Jobs:
        called = False

        async def start(self, **kwargs):
            self.called = True
            raise AssertionError("SDK creation must not run after failed preflight")

    jobs = Jobs()
    config = SimpleNamespace(
        username="user@example.test",
        cluster_name="alpha",
        org_name="team",
        project_name="default",
        clusters={
            "alpha": SimpleNamespace(
                orgs=["team"],
                presets={"cpu-small": SimpleNamespace(scheduler_enabled=False)},
            )
        },
        projects={
            "default": SimpleNamespace(
                cluster_name="alpha", org_name="team", name="default"
            )
        },
    )
    sdk = SimpleNamespace(
        config=config,
        jobs=jobs,
        parse=SimpleNamespace(remote_image=lambda value, **kwargs: value),
    )
    token = set_client_provider(Provider(sdk))
    mcp = MCPServer("ledger-preflight-test")
    register(mcp)
    try:
        with pytest.raises(Exception, match="ledger parent"):
            await mcp._tool_manager._tools["run_job"].fn("ubuntu:24.04", "cpu-small")
    finally:
        reset_client_provider(token)
    assert jobs.called is False
    assert list(real_parent.iterdir()) == []
