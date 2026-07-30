# mypy: disable-error-code="no-untyped-def"

from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from mcp.server.fastmcp import FastMCP

from apolo_mcp.errors import ApoloToolError
from apolo_mcp.tools.flow import (
    MAX_LIST,
    FlowScope,
    LocalFlowAPIProvider,
    register,
    reset_flow_api_provider,
    set_flow_api_provider,
)


@dataclass(frozen=True)
class Job:
    job_id: str
    raw_id: str
    status: str = "running"


@dataclass(frozen=True)
class JobList:
    jobs: tuple[Job, ...]
    truncated: bool


@dataclass(frozen=True)
class RunResult:
    job_id: str
    jobs: tuple[Job, ...]


@dataclass(frozen=True)
class Log:
    raw_id: str
    data: bytes
    chunks: int
    truncated: bool


@dataclass(frozen=True)
class Bake:
    id: str
    status: str = "running"
    tasks: tuple[dict[str, str], ...] = ()
    tasks_truncated: bool = False


@dataclass(frozen=True)
class BakeList:
    bakes: tuple[Bake, ...]
    truncated: bool


class FakeAPI:
    def __init__(self):
        self.calls = []

    def called(self, name, args, kwargs):
        self.calls.append((name, args, kwargs))

    async def live_list(self, *args, **kwargs):
        self.called("live_list", args, kwargs)
        return JobList((Job("worker", "job-1"),), False)

    async def live_get(self, *args, **kwargs):
        self.called("live_get", args, kwargs)
        return (Job("worker", "job-1"),)

    async def live_run(self, job_id, *, suffix=None, params=None, timeout=300.0):
        args = (job_id,)
        kwargs = {
            "suffix": suffix,
            "params": params,
            "timeout": timeout,
        }
        self.called("live_run", args, kwargs)
        return RunResult("worker", (Job("worker", "job-1"), Job("worker", "job-2")))

    async def live_logs(self, *args, **kwargs):
        self.called("live_logs", args, kwargs)
        return Log("job-1", b"hello token=unsafe\ntail", 2, False)

    async def live_wait(self, *args, **kwargs):
        self.called("live_wait", args, kwargs)
        return (Job("worker", "job-1", "succeeded"),)

    async def live_kill(self, *args, **kwargs):
        self.called("live_kill", args, kwargs)
        return (Job("worker", "job-1", "cancelled"),)

    async def live_kill_all(self, *args, **kwargs):
        self.called("live_kill_all", args, kwargs)
        return JobList((Job("worker", "job-1", "cancelled"),), False)

    async def bake_start(self, *args, **kwargs):
        self.called("bake_start", args, kwargs)
        return Bake("bake-1", tasks=({"id": "train"},))

    async def bake_list(self, *args, **kwargs):
        self.called("bake_list", args, kwargs)
        return BakeList((Bake("bake-1"),), False)

    async def bake_get(self, *args, **kwargs):
        self.called("bake_get", args, kwargs)
        return Bake("bake-1")

    async def bake_logs(self, *args, **kwargs):
        self.called("bake_logs", args, kwargs)
        return Log("job-task", b"authorization: Bearer-unsafe", 1, False)

    async def bake_wait(self, *args, **kwargs):
        self.called("bake_wait", args, kwargs)
        return Bake("bake-1", "succeeded")

    async def bake_cancel(self, *args, **kwargs):
        self.called("bake_cancel", args, kwargs)
        return Bake("bake-1", "cancelled")

    async def bake_restart(self, *args, **kwargs):
        self.called("bake_restart", args, kwargs)
        return Bake("bake-1", "pending")


class Provider:
    def __init__(self, api):
        self.value = api
        self.scopes = []

    @asynccontextmanager
    async def api(self, scope):
        self.scopes.append(scope)
        yield self.value


@pytest.fixture()
def tools(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "full")
    monkeypatch.setenv("APOLO_MCP_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    workspace = tmp_path / "workspace"
    config_dir = workspace / ".apolo"
    config_dir.mkdir(parents=True)
    config = config_dir / "live.yml"
    project_config = config_dir / "project.yml"
    config.write_text("kind: live")
    project_config.write_text("id: demo")
    monkeypatch.chdir(workspace)
    scope = {
        "cluster": "alpha",
        "org": "team",
        "project": "default",
        "workspace_path": str(workspace),
    }
    fake = FakeAPI()
    provider = Provider(fake)
    token = set_flow_api_provider(provider)
    mcp = FastMCP("flow-test")
    register(mcp)
    try:
        yield mcp._tool_manager._tools, fake, provider, scope, tmp_path
    finally:
        reset_flow_api_provider(token)


def fn(tools, name):
    return tools[0][name].fn


def test_tools_and_destructive_annotations(tools):
    registered = tools[0]
    expected = {
        "flow_live_list",
        "flow_live_run",
        "flow_live_get",
        "flow_live_logs",
        "flow_live_wait",
        "flow_live_kill",
        "flow_live_kill_all",
        "flow_bake_start",
        "flow_bake_list",
        "flow_bake_get",
        "flow_bake_logs",
        "flow_bake_wait",
        "flow_bake_cancel",
        "flow_bake_restart",
    }
    assert expected <= registered.keys()
    for name in (
        "flow_live_kill",
        "flow_live_kill_all",
        "flow_bake_cancel",
        "flow_bake_restart",
    ):
        assert registered[name].annotations.destructiveHint is True
    assert registered["flow_live_run"].annotations.destructiveHint is False
    assert registered["flow_bake_start"].annotations.destructiveHint is False
    for name in expected:
        parameters = registered[name].parameters["properties"]
        assert "allowed_workspace_root" not in parameters
        assert "config_path" not in parameters
        assert "project_path" not in parameters
        if name == "flow_live_run":
            assert "args" not in parameters
        assert "workspace_path is the Flow project root" in registered[name].description
    assert ".apolo/live.yml" in registered["flow_live_run"].description
    assert ".apolo/<batch>.yml" in registered["flow_bake_start"].description


async def test_reads_are_structured_bounded_and_redacted(tools):
    _, fake, provider, scope, _ = tools
    listed = await fn(tools, "flow_live_list")(**scope, limit=1)
    assert listed["items"][0]["raw_id"] == "job-1"
    assert listed["context"] == {
        "cluster": "alpha",
        "org": "team",
        "project": "default",
    }
    assert provider.scopes[0].workspace_path.name == "workspace"
    await fn(tools, "flow_live_get")("worker", **scope)
    live_log = await fn(tools, "flow_live_logs")("worker", **scope, max_bytes=100)
    assert "unsafe" not in live_log["log"]["logs"]
    assert live_log["log"]["redacted"] is True
    await fn(tools, "flow_live_wait")("worker", **scope)
    await fn(tools, "flow_bake_list")(**scope)
    await fn(tools, "flow_bake_get")("bake-1", **scope)
    bake_log = await fn(tools, "flow_bake_logs")("bake-1", "train", **scope)
    assert "unsafe" not in bake_log["log"]["logs"]
    await fn(tools, "flow_bake_wait")("bake-1", **scope)
    assert {call[0] for call in fake.calls} >= {
        "live_list",
        "live_get",
        "live_logs",
        "live_wait",
        "bake_list",
        "bake_get",
        "bake_logs",
        "bake_wait",
    }


async def test_scope_escape_and_hard_caps_are_rejected_before_provider(tools):
    _, fake, _, scope, tmp_path = tools
    outside = tmp_path / "outside"
    (outside / ".apolo").mkdir(parents=True)
    escaped = {**scope, "workspace_path": str(outside)}
    with pytest.raises(PermissionError, match="must be beneath"):
        await fn(tools, "flow_live_list")(**escaped)
    with pytest.raises(ValueError, match="limit"):
        await fn(tools, "flow_live_list")(**scope, limit=MAX_LIST + 1)
    with pytest.raises(ValueError, match="max_bytes"):
        await fn(tools, "flow_live_logs")("worker", **scope, max_bytes=1_000_001)
    assert fake.calls == []


async def test_every_write_uses_policy_without_approval_parameter(tools, monkeypatch):
    _, fake, _, scope, _ = tools
    writes = [
        ("flow_live_run", ("worker",)),
        ("flow_live_kill", ("worker",)),
        ("flow_live_kill_all", ()),
        ("flow_bake_start", ("train",)),
        ("flow_bake_cancel", ("bake-1",)),
        ("flow_bake_restart", ("bake-1",)),
    ]
    for name, _ in writes:
        assert "approved" not in tools[0][name].parameters["properties"]
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "read-only")
    with pytest.raises(PermissionError, match="server policy"):
        await fn(tools, "flow_live_run")("worker", **scope)
    mutation_calls = {
        "live_run",
        "live_kill",
        "live_kill_all",
        "bake_start",
        "bake_cancel",
        "bake_restart",
    }
    assert not ({call[0] for call in fake.calls} & mutation_calls)


async def test_writes_call_facade_and_immediately_ledger_new_ids(tools):
    _, fake, _, scope, tmp_path = tools
    await fn(tools, "flow_live_run")("worker", **scope)
    await fn(tools, "flow_live_kill")("worker", **scope)
    await fn(tools, "flow_live_kill_all")(**scope)
    await fn(tools, "flow_bake_start")("train", **scope)
    await fn(tools, "flow_bake_cancel")("bake-1", **scope)
    await fn(tools, "flow_bake_restart")("bake-1", **scope)
    assert {call[0] for call in fake.calls} >= {
        "live_run",
        "live_kill",
        "live_kill_all",
        "bake_start",
        "bake_cancel",
        "bake_restart",
    }
    ledger = (tmp_path / "ledger.jsonl").read_text()
    assert '"resource_id":"job-1"' in ledger
    assert '"resource_id":"job-2"' in ledger
    assert '"resource_id":"bake-1"' in ledger


async def test_failed_bake_start_recovers_and_journals_created_id(tools):
    _, fake, _, scope, tmp_path = tools

    async def fail(*args, **kwargs):
        raise RuntimeError(
            "batch runner exited before bake "
            "'bake-11111111-2222-3333-4444-555555555555' reached terminal state"
        )

    async def correlated(*, tags, **kwargs):
        assert any(tag.startswith("apolo-mcp-correlation-") for tag in tags)
        return BakeList((Bake("bake-11111111-2222-3333-4444-555555555555"),), False)

    fake.bake_start = fail
    fake.bake_list = correlated
    with pytest.raises(ApoloToolError, match="creating and journaling"):
        await fn(tools, "flow_bake_start")("train", **scope)

    ledger = (tmp_path / "ledger.jsonl").read_text()
    assert '"resource_id":"bake-11111111-2222-3333-4444-555555555555"' in ledger


async def test_failed_live_run_journals_only_new_job_ids(tools):
    _, fake, _, scope, tmp_path = tools
    calls = 0

    async def live_get(job_id, suffix=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return (Job("worker", "job-before"),)
        return (Job("worker", "job-before"), Job("worker", "job-created"))

    async def fail(*args, **kwargs):
        raise TimeoutError("timed out starting live job")

    fake.live_get = live_get
    fake.live_run = fail
    with pytest.raises(ApoloToolError, match="creating and journaling job-created"):
        await fn(tools, "flow_live_run")("worker", **scope)

    ledger = (tmp_path / "ledger.jsonl").read_text()
    assert '"resource_id":"job-created"' in ledger
    assert '"resource_id":"job-before"' not in ledger


async def test_provider_errors_are_normalized(tools):
    _, fake, _, scope, _ = tools

    async def fail(*args, **kwargs):
        raise RuntimeError("token=should-not-leak")

    fake.live_get = fail
    with pytest.raises(ApoloToolError) as info:
        await fn(tools, "flow_live_get")("worker", **scope)
    assert info.value.operation == "flow_live_get"
    assert "should-not-leak" not in str(info.value)


async def test_default_provider_uses_released_explicit_context_factory(
    tmp_path, monkeypatch
):
    provider = LocalFlowAPIProvider()
    workspace = tmp_path / "workspace"
    flow_dir = workspace / ".apolo"
    flow_dir.mkdir(parents=True)
    sdk_config = tmp_path / "sdk-config"
    sdk_config.mkdir()
    scope = FlowScope(
        cluster="cluster-a",
        org="org-a",
        project="project-a",
        allowed_workspace_root=workspace,
        workspace_path=workspace,
    )
    seen = {}

    @asynccontextmanager
    async def fake_open_flow_api(**kwargs):
        seen.update(kwargs)
        yield "flow-api"

    monkeypatch.setenv("APOLO_CONFIG", str(sdk_config))
    monkeypatch.setattr(
        "apolo_mcp.tools.flow.open_flow_api",
        fake_open_flow_api,
    )
    async with provider.api(scope) as api:
        assert api == "flow-api"
    assert seen == {
        "cluster": "cluster-a",
        "org": "org-a",
        "project": "project-a",
        "allowed_workspace_root": workspace,
        "config_path": sdk_config,
        "project_path": workspace,
    }
