# mypy: disable-error-code="no-untyped-def"

from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from mcp.server.fastmcp import FastMCP

from apolo_mcp.errors import ApoloToolError
from apolo_mcp.tools.flow import (
    MAX_LIST,
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

    async def live_run(self, *args, **kwargs):
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
    monkeypatch.setenv("APOLO_MCP_ENABLE_HIGH_RISK", "true")
    monkeypatch.setenv("APOLO_MCP_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    workspace = tmp_path / "workspace"
    config_dir = workspace / ".apolo"
    config_dir.mkdir(parents=True)
    config = config_dir / "live.yml"
    project_config = config_dir / "project.yml"
    config.write_text("kind: live")
    project_config.write_text("id: demo")
    scope = {
        "cluster": "alpha",
        "org": "team",
        "project": "default",
        "allowed_workspace_root": str(tmp_path),
        "workspace_path": str(workspace),
        "config_path": str(config),
        "project_path": str(project_config),
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


async def test_reads_are_structured_bounded_and_redacted(tools):
    _, fake, provider, scope, _ = tools
    listed = await fn(tools, "flow_live_list")(**scope, limit=1)
    assert listed["items"][0]["raw_id"] == "job-1"
    assert listed["context"] == {
        "cluster": "alpha",
        "org": "team",
        "project": "default",
    }
    assert provider.scopes[0].config_path.name == "live.yml"
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
    outside = tmp_path.parent / "outside-flow.yml"
    outside.write_text("kind: live")
    escaped = {**scope, "config_path": str(outside)}
    with pytest.raises(ValueError, match="escapes allowed_workspace_root"):
        await fn(tools, "flow_live_list")(**escaped)
    with pytest.raises(ValueError, match="limit"):
        await fn(tools, "flow_live_list")(**scope, limit=MAX_LIST + 1)
    with pytest.raises(ValueError, match="max_bytes"):
        await fn(tools, "flow_live_logs")("worker", **scope, max_bytes=1_000_001)
    assert fake.calls == []


async def test_every_write_requires_approval_and_policy(tools, monkeypatch):
    _, fake, _, scope, _ = tools
    writes = [
        ("flow_live_run", ("worker",)),
        ("flow_live_kill", ("worker",)),
        ("flow_live_kill_all", ()),
        ("flow_bake_start", ("train",)),
        ("flow_bake_cancel", ("bake-1",)),
        ("flow_bake_restart", ("bake-1",)),
    ]
    for name, args in writes:
        with pytest.raises(PermissionError, match="approved=true"):
            await fn(tools, name)(*args, **scope)
    monkeypatch.setenv("APOLO_MCP_ENABLE_HIGH_RISK", "false")
    with pytest.raises(PermissionError, match="server policy"):
        await fn(tools, "flow_live_run")("worker", **scope, approved=True)
    assert fake.calls == []


async def test_writes_call_facade_and_immediately_ledger_new_ids(tools):
    _, fake, _, scope, tmp_path = tools
    await fn(tools, "flow_live_run")("worker", **scope, approved=True)
    await fn(tools, "flow_live_kill")("worker", **scope, approved=True)
    await fn(tools, "flow_live_kill_all")(**scope, approved=True)
    await fn(tools, "flow_bake_start")("train", **scope, approved=True)
    await fn(tools, "flow_bake_cancel")("bake-1", **scope, approved=True)
    await fn(tools, "flow_bake_restart")("bake-1", **scope, approved=True)
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


async def test_provider_errors_are_normalized(tools):
    _, fake, _, scope, _ = tools

    async def fail(*args, **kwargs):
        raise RuntimeError("token=should-not-leak")

    fake.live_get = fail
    with pytest.raises(ApoloToolError) as info:
        await fn(tools, "flow_live_get")("worker", **scope)
    assert info.value.operation == "flow_live_get"
    assert "should-not-leak" not in str(info.value)


async def test_default_provider_reports_explicit_bootstrap_prerequisite(
    monkeypatch,
):
    provider = LocalFlowAPIProvider()
    scope = type("Scope", (), {})()
    monkeypatch.setattr(
        "apolo_mcp.tools.flow.importlib.import_module",
        lambda name: type("FlowModule", (), {"FlowAPI": object})(),
    )
    with pytest.raises(
        RuntimeError, match="public factory for explicit cluster/org/project"
    ):
        async with provider.api(scope):
            pass


async def test_default_provider_reports_missing_facade(monkeypatch):
    provider = LocalFlowAPIProvider()
    scope = type("Scope", (), {})()

    def missing(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(
        "apolo_mcp.tools.flow.importlib.import_module",
        missing,
    )
    with pytest.raises(RuntimeError, match="install a compatible release"):
        async with provider.api(scope):
            pass
