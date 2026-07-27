# mypy: disable-error-code="no-untyped-def"

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import apolo_sdk
import pytest
from mcp.server.fastmcp import FastMCP

from apolo_mcp._client import reset_client_provider, set_client_provider
from apolo_mcp.errors import ApoloToolError
from apolo_mcp.ledger import Ledger
from apolo_mcp.tools.jobs import (
    DiskVolumeInput,
    SecretFileInput,
    StorageVolumeInput,
    register,
)


class FakeProvider:
    def __init__(self, sdk):
        self.sdk = sdk

    @asynccontextmanager
    async def client(self):
        yield self.sdk


class FakeJobs:
    def __init__(self, job):
        self.job = job
        self.list_items = [job]
        self.start = AsyncMock(return_value=job)
        self.status = AsyncMock(return_value=job)
        self.bump_life_span = AsyncMock()
        self.send_signal = AsyncMock()
        self.save = AsyncMock()
        self.kill = AsyncMock()
        self.list_kwargs = None
        self.log_chunks = [b"hello\n", b"token=unsafe\n", b"tail\n"]
        self.samples = [
            apolo_sdk.JobTelemetry(0.25, 100, 1.0, 10, 20),
            apolo_sdk.JobTelemetry(0.75, 300, 2.0, 30, 40),
        ]

    async def list(self, **kwargs):
        self.list_kwargs = kwargs
        for item in self.list_items:
            yield item

    async def monitor(self, job_id, **kwargs):
        for chunk in self.log_chunks:
            yield chunk

    async def top(self, job_id, **kwargs):
        for sample in self.samples:
            yield sample


def make_job(status=apolo_sdk.JobStatus.RUNNING):
    history = SimpleNamespace(
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        started_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        finished_at=None,
        exit_code=None,
        reason="",
    )
    container = SimpleNamespace(
        image="image:tag",
        entrypoint="python",
        command="main.py",
        working_dir="/work",
    )
    return SimpleNamespace(
        id="job-1",
        name="demo",
        owner="alice",
        cluster_name="alpha",
        org_name="team",
        project_name="default",
        status=status,
        container=container,
        preset_name="cpu-small",
        scheduler_enabled=True,
        restart_policy=apolo_sdk.JobRestartPolicy.NEVER,
        life_span=3600,
        schedule_timeout=60,
        energy_schedule_name="green",
        priority=apolo_sdk.JobPriority.NORMAL,
        history=history,
        description="test",
        tags=("unit",),
    )


@pytest.fixture()
def tools(monkeypatch, tmp_path):
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "full")
    monkeypatch.setenv("APOLO_MCP_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    config = SimpleNamespace(
        cluster_name="alpha",
        org_name="team",
        project_name="default",
        clusters={
            "alpha": SimpleNamespace(
                orgs=["team"],
                presets={
                    "gpu-small": SimpleNamespace(scheduler_enabled=True),
                    "preset": SimpleNamespace(scheduler_enabled=False),
                },
            ),
            "beta": SimpleNamespace(
                orgs=["other"],
                presets={"gpu-small": SimpleNamespace(scheduler_enabled=True)},
            ),
        },
        projects={
            "one": SimpleNamespace(
                cluster_name="alpha", org_name="team", name="default"
            ),
            "two": SimpleNamespace(
                cluster_name="beta", org_name="other", name="research"
            ),
        },
    )
    parse = SimpleNamespace(
        remote_image=lambda value, **kwargs: f"remote:{value}",
        str_to_uri=lambda value: value,
    )
    jobs = FakeJobs(make_job())
    images = SimpleNamespace(
        digest=AsyncMock(side_effect=apolo_sdk.ResourceNotFound("missing"))
    )
    sdk = SimpleNamespace(config=config, parse=parse, jobs=jobs, images=images)
    token = set_client_provider(FakeProvider(sdk))
    mcp = FastMCP("jobs-test")
    register(mcp)
    try:
        yield mcp._tool_manager._tools, sdk
    finally:
        reset_client_provider(token)


def fn(tools, name):
    return tools[0][name].fn


async def test_run_job_serializes_every_safe_field_and_override(tools):
    result = await fn(tools, "run_job")(
        "ubuntu:24.04",
        "gpu-small",
        "python",
        "train.py",
        "/work",
        {"MODE": "test"},
        [StorageVolumeInput(storage="storage:data", container_path="/data")],
        {"API_TOKEN": "secret:api-token"},
        [SecretFileInput(secret="secret:key", container_path="/run/key")],
        [DiskVolumeInput(disk="disk:cache", container_path="/cache")],
        8080,
        False,
        3600,
        "train",
        ["ml"],
        "description",
        "high",
        True,
        True,
        60,
        "on-failure",
        "green",
        "beta",
        "other",
        "research",
    )
    kwargs = tools[1].jobs.start.await_args.kwargs
    assert kwargs["cluster_name"] == "beta"
    assert kwargs["org_name"] == "other"
    assert kwargs["project_name"] == "research"
    assert kwargs["image"] == "remote:ubuntu:24.04"
    assert kwargs["working_dir"] == "/work"
    assert kwargs["http"].port == 8080
    assert kwargs["http"].requires_auth is False
    assert kwargs["priority"] is apolo_sdk.JobPriority.HIGH
    assert kwargs["restart_policy"] == apolo_sdk.JobRestartPolicy.ON_FAILURE
    assert str(kwargs["volumes"][0].storage_uri) == (
        "storage://beta/other/research/data"
    )
    assert kwargs["volumes"][0].container_path == "/data"
    assert kwargs["secret_env"]["API_TOKEN"] == (
        "secret://beta/other/research/api-token"
    )
    assert str(kwargs["secret_files"][0].secret_uri) == (
        "secret://beta/other/research/key"
    )
    assert kwargs["secret_files"][0].container_path == "/run/key"
    assert str(kwargs["disk_volumes"][0].disk_uri) == (
        "disk://beta/other/research/cache"
    )
    assert kwargs["disk_volumes"][0].container_path == "/cache"
    assert result["context"] == {
        "cluster": "beta",
        "org": "other",
        "project": "research",
    }
    assert "api-token" not in repr(result)


async def test_run_job_rejects_secret_values_and_bounds_before_sdk(tools):
    with pytest.raises(ValueError, match="secret: references"):
        await fn(tools, "run_job")("image", "preset", secret_env={"TOKEN": "plaintext"})
    with pytest.raises(ValueError, match="between 1 and 65535"):
        await fn(tools, "run_job")("image", "preset", http_port=0)
    with pytest.raises(ValueError, match="sensitive environment names"):
        await fn(tools, "run_job")("image", "preset", env={"PASSWORD": "plaintext"})
    tools[1].jobs.start.assert_not_awaited()


async def test_run_job_resolves_platform_image_and_rejects_cross_context_uri(tools):
    await fn(tools, "run_job")("image:model", "gpu-small")
    assert tools[1].jobs.start.await_args.kwargs["image"] == (
        "remote:image://alpha/team/default/model"
    )
    tools[1].jobs.start.reset_mock()
    with pytest.raises(ApoloToolError, match="does not belong"):
        await fn(tools, "run_job")("image://beta/other/research/model", "gpu-small")
    tools[1].jobs.start.assert_not_awaited()


async def test_run_job_validates_scheduler_expectation_from_preset(tools):
    with pytest.raises(ApoloToolError, match="cannot override"):
        await fn(tools, "run_job")("ubuntu:24.04", "gpu-small", scheduler_enabled=False)
    tools[1].jobs.start.assert_not_awaited()


async def test_run_job_rejects_cross_context_volume_uri(tools):
    with pytest.raises(ApoloToolError, match="does not belong"):
        await fn(tools, "run_job")(
            "ubuntu:24.04",
            "gpu-small",
            storage_volumes=[
                StorageVolumeInput(
                    storage="storage://beta/other/research/data",
                    container_path="/data",
                )
            ],
        )
    tools[1].jobs.start.assert_not_awaited()


async def test_list_filters_limits_serialization_and_context(tools):
    result = await fn(tools, "list_jobs")(
        ["running"],
        "demo",
        ["unit"],
        ["alice"],
        "2026-01-01T00:00:00Z",
        "2026-01-02T00:00:00Z",
        7,
        "alpha",
        "team",
        "default",
    )
    kwargs = tools[1].jobs.list_kwargs
    assert kwargs["statuses"] == {apolo_sdk.JobStatus.RUNNING}
    assert kwargs["tags"] == ["unit"]
    assert kwargs["owners"] == ["alice"]
    assert kwargs["limit"] == 8
    assert result["items"][0]["id"] == "job-1"
    assert result["context"]["project"] == "default"
    assert result["truncated"] is False


async def test_list_uses_lookahead_for_truthful_truncation(tools):
    tools[1].jobs.list_items = [make_job(), make_job()]
    result = await fn(tools, "list_jobs")(limit=1)
    assert tools[1].jobs.list_kwargs["limit"] == 2
    assert len(result["items"]) == 1
    assert result["truncated"] is True


async def test_list_limits_are_validated_before_sdk(tools):
    with pytest.raises(ValueError, match="between 1 and 100"):
        await fn(tools, "list_jobs")(limit=101)
    assert tools[1].jobs.list_kwargs is None


async def test_get_and_wait_terminal(tools):
    got = await fn(tools, "get_job")("job-1")
    assert got["job"]["workdir"] == "/work"
    assert got["job"]["scheduler_enabled"] is True
    assert got["job"]["energy_schedule"] == "green"
    tools[1].jobs.status.return_value = make_job(apolo_sdk.JobStatus.SUCCEEDED)
    waited = await fn(tools, "wait_for_job")("job-1", 1, 0.01)
    assert waited["terminal"] is True
    assert waited["timed_out"] is False


async def test_wait_returns_bounded_timeout_summary(tools):
    waited = await fn(tools, "wait_for_job")("job-1", 0.001, 0.001)
    assert waited["terminal"] is False
    assert waited["timed_out"] is True
    assert waited["job"]["status"] == "running"


async def test_get_rejects_job_outside_explicit_context(tools):
    tools[1].jobs.status.return_value.cluster_name = "beta"
    with pytest.raises(ApoloToolError, match="does not belong"):
        await fn(tools, "get_job")("job-1")


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("get_job_logs", ("job-1",)),
        ("get_job_telemetry", ("job-1",)),
        ("bump_job_life_span", ("job-1", 60)),
        ("send_job_signal", ("job-1",)),
        ("save_job_image", ("job-1", "repo:saved")),
        ("kill_job", ("job-1",)),
    ],
)
async def test_id_based_operations_reject_cross_context_job(tools, tool_name, args):
    tools[1].jobs.status.return_value.cluster_name = "beta"
    with pytest.raises(ApoloToolError, match="does not belong"):
        await fn(tools, tool_name)(*args)


async def test_logs_are_bounded_truncated_and_redacted(tools):
    result = await fn(tools, "get_job_logs")("job-1", 1, 32, 2)
    assert result["truncated"] is True
    assert "unsafe" not in result["logs"]
    assert "<redacted>" in result["logs"]
    assert len(result["logs"].encode()) <= 32
    assert result["bytes"] <= 32
    with pytest.raises(ValueError, match="max_bytes"):
        await fn(tools, "get_job_logs")("job-1", max_bytes=1_000_001)


async def test_telemetry_summary_raw_and_cap(tools):
    result = await fn(tools, "get_job_telemetry")("job-1", 1, 2, True)
    assert result["summary"]["sample_count"] == 2
    assert result["summary"]["cpu"] == {"min": 0.25, "max": 0.75, "mean": 0.5}
    assert len(result["raw"]) == 2
    assert result["truncated"] is True
    with pytest.raises(ValueError, match="max_samples"):
        await fn(tools, "get_job_telemetry")("job-1", max_samples=101)


@pytest.mark.parametrize(
    ("tool_name", "args", "mock_name"),
    [
        ("bump_job_life_span", ("job-1", 60), "bump_life_span"),
        ("send_job_signal", ("job-1",), "send_signal"),
        ("save_job_image", ("job-1", "repo:saved"), "save"),
        ("kill_job", ("job-1",), "kill"),
    ],
)
async def test_each_write_calls_sdk_and_returns_context(
    tools, tool_name, args, mock_name
):
    result = await fn(tools, tool_name)(*args)
    getattr(tools[1].jobs, mock_name).assert_awaited_once()
    assert result["id"] == "job-1"
    assert result["context"]["cluster"] == "alpha"


async def test_managed_save_rejects_preexisting_unowned_image_target(
    tools, monkeypatch
):
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "managed")
    exact_context = {"cluster": "alpha", "org": "team", "project": "default"}
    Ledger().append(
        resource_type="job",
        resource_id="job-1",
        operation="run_job",
        action="created",
        **exact_context,
    )
    tools[1].images.digest.side_effect = None
    tools[1].images.digest.return_value = "sha256:existing"

    with pytest.raises(ApoloToolError, match="no active creation lifecycle"):
        await fn(tools, "save_job_image")("job-1", "repo:saved")
    tools[1].jobs.save.assert_not_awaited()

    Ledger().append(
        resource_type="image",
        resource_id="remote:repo:saved",
        operation="save_job_image",
        action="created",
        **exact_context,
    )
    await fn(tools, "save_job_image")("job-1", "repo:saved")
    tools[1].jobs.save.assert_awaited_once()


async def test_policy_blocks_writes_before_sdk(tools, monkeypatch):
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "read-only")
    with pytest.raises(ApoloToolError, match="read-only server policy"):
        await fn(tools, "kill_job")("job-1")
    tools[1].jobs.kill.assert_not_awaited()


async def test_write_schemas_have_no_model_supplied_approval(tools):
    for name in (
        "run_job",
        "bump_job_life_span",
        "send_job_signal",
        "save_job_image",
        "kill_job",
    ):
        assert "approved" not in tools[0][name].parameters["properties"]


async def test_sdk_errors_are_normalized_with_context(tools):
    tools[1].jobs.status.side_effect = apolo_sdk.ResourceNotFound("missing")
    with pytest.raises(ApoloToolError) as info:
        await fn(tools, "get_job")("job-missing")
    assert info.value.operation == "get_job"
    assert info.value.resource == "job-missing"
    assert info.value.context is not None
    assert info.value.context["project"] == "default"


def test_inline_annotations_are_exact(tools):
    assert tools[0]["get_job"].annotations.readOnlyHint is True
    assert tools[0]["run_job"].annotations.destructiveHint is False
    assert tools[0]["run_job"].annotations.idempotentHint is False
    assert tools[0]["kill_job"].annotations.destructiveHint is True
    assert tools[0]["kill_job"].annotations.idempotentHint is True
    assert "exec_job" not in tools[0]
