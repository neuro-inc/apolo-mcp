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
from apolo_mcp.tools.disks import register


class Provider:
    def __init__(self, sdk):
        self.sdk = sdk

    @asynccontextmanager
    async def client(self):
        yield self.sdk


def disk(id="disk-1", project="default"):
    return apolo_sdk.Disk(
        id=id,
        storage=2 * 1024**3,
        owner="alice",
        status=apolo_sdk.Disk.Status.READY,
        cluster_name="alpha",
        org_name="team",
        project_name=project,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class Disks:
    def __init__(self):
        self.items = [disk(), disk("disk-2")]
        self.create = AsyncMock(return_value=disk())
        self.get = AsyncMock(return_value=disk())
        self.rm = AsyncMock()

    async def list(self, **kwargs):
        for item in self.items:
            yield item


@pytest.fixture()
def tools(monkeypatch, tmp_path):
    monkeypatch.setenv("APOLO_MCP_ENABLE_HIGH_RISK", "true")
    monkeypatch.setenv("APOLO_MCP_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    cfg = SimpleNamespace(
        cluster_name="alpha",
        org_name="team",
        project_name="default",
        clusters={"alpha": SimpleNamespace(orgs=["team"])},
        projects={
            "p": SimpleNamespace(cluster_name="alpha", org_name="team", name="default")
        },
    )
    sdk = SimpleNamespace(config=cfg, disks=Disks())
    token = set_client_provider(Provider(sdk))
    mcp = FastMCP("disk-test")
    register(mcp)
    try:
        yield mcp._tool_manager._tools, sdk, tmp_path
    finally:
        reset_client_provider(token)


def fn(tools, name):
    return tools[0][name].fn


async def test_list_bound_create_context_and_ledger(tools):
    result = await fn(tools, "list_disks")(1)
    assert result["truncated"] is True
    created = await fn(tools, "create_disk")(2, "data", 1, True)
    assert created["disk"]["storage_bytes"] == 2 * 1024**3
    kwargs = tools[1].disks.create.await_args.kwargs
    assert kwargs["cluster_name"] == "alpha" and kwargs["org_name"] == "team"
    assert '"resource_id":"disk-1"' in (tools[2] / "ledger.jsonl").read_text()


async def test_create_bounds_before_sdk(tools):
    with pytest.raises(ValueError, match="size_gb"):
        await fn(tools, "create_disk")(0, approved=True)
    await fn(tools, "create_disk")(1, timeout_unused_hours=9000, approved=True)
    assert tools[1].disks.create.await_count == 1
    with pytest.raises(ValueError, match="10 years"):
        await fn(tools, "create_disk")(1, timeout_unused_hours=90000, approved=True)
    assert tools[1].disks.create.await_count == 1


async def test_delete_exact_approval_and_ledger_cleanup(tools):
    with pytest.raises(PermissionError, match="approved=true"):
        await fn(tools, "delete_disk")("disk-1")
    with pytest.raises(PermissionError, match="approved=true"):
        await fn(tools, "create_disk")(2)
    await fn(tools, "create_disk")(2, approved=True)
    await fn(tools, "delete_disk")("disk-1", automatic_cleanup=True)
    tools[1].disks.rm.assert_awaited_once()
    tools[1].disks.get.return_value = disk("disk-2")
    with pytest.raises(ApoloToolError, match="no exact ledger ownership"):
        await fn(tools, "delete_disk")("disk-2", automatic_cleanup=True)


async def test_delete_rejects_alias_and_annotations(tools):
    tools[1].disks.get.return_value = disk("actual-id")
    with pytest.raises(ApoloToolError, match="name or alias"):
        await fn(tools, "delete_disk")("friendly", approved=True)
    assert tools[0]["list_disks"].annotations.readOnlyHint is True
    assert tools[0]["delete_disk"].annotations.destructiveHint is True
