import stat
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import FastMCP

from apolo_mcp._client import reset_client_provider, set_client_provider
from apolo_mcp.tools import service_accounts as service_account_tools
from apolo_mcp.tools.service_accounts import register


@asynccontextmanager
async def iterator(items):
    async def generate():
        for item in items:
            yield item

    yield generate()


class Provider:
    def __init__(self, sdk):
        self.sdk = sdk

    @asynccontextmanager
    async def client(self):
        yield self.sdk


def config():
    return SimpleNamespace(
        cluster_name="c",
        org_name="o",
        project_name="p",
        clusters={"c": SimpleNamespace(orgs={"o": object()})},
        projects={"p": SimpleNamespace(cluster_name="c", org_name="o", name="p")},
    )


def account(id="sa-1"):
    return SimpleNamespace(
        id=id,
        name="robot",
        owner="alice",
        role="user",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        default_cluster="c",
        default_org="o",
        default_project="p",
    )


@pytest.fixture
def tools(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("APOLO_MCP_ENABLE_HIGH_RISK", "true")
    monkeypatch.setenv("APOLO_MCP_ALLOWED_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("APOLO_MCP_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    service_accounts = MagicMock()
    service_accounts.list = lambda: iterator([account(), account("sa-2")])
    service_accounts.get = AsyncMock(return_value=account())
    service_accounts.create = AsyncMock(
        return_value=(account(), "one-time-private-token")
    )
    service_accounts.rm = AsyncMock()
    secrets = MagicMock()
    secrets.list = lambda **kwargs: iterator([])
    secrets.add = AsyncMock()
    secrets.get = AsyncMock(side_effect=AssertionError("must never retrieve secrets"))
    sdk = SimpleNamespace(
        config=config(), service_accounts=service_accounts, secrets=secrets
    )
    token = set_client_provider(Provider(sdk))
    mcp = FastMCP("service-accounts-test")
    register(mcp)
    try:
        yield mcp, sdk, tmp_path
    finally:
        reset_client_provider(token)


def fn(mcp, name):
    return mcp._tool_manager._tools[name].fn


async def test_create_sinks_token_to_exact_protected_file(tools):
    mcp, sdk, tmp_path = tools
    result = await fn(mcp, "create_service_account")(
        "file", "credentials/robot.token", name="robot", approved=True
    )
    target = tmp_path / "credentials/robot.token"
    assert target.read_text() == "one-time-private-token"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert "one-time-private-token" not in repr(result)
    assert result["destination"] == {
        "type": "file",
        "path": str(target),
        "mode": "0600",
    }
    ledger = (tmp_path / "ledger.jsonl").read_text()
    assert '"resource_type":"service_account"' in ledger
    sdk.secrets.get.assert_not_awaited()


async def test_file_sink_is_preflighted_before_remote_creation(tools):
    mcp, sdk, tmp_path = tools
    target = tmp_path / "exists"
    target.write_text("do-not-overwrite")
    with pytest.raises(Exception, match="exists"):
        await fn(mcp, "create_service_account")("file", str(target), approved=True)
    assert target.read_text() == "do-not-overwrite"
    sdk.service_accounts.create.assert_not_awaited()


async def test_sink_failure_leaves_created_account_in_cleanup_ledger(
    tools, monkeypatch
):
    mcp, sdk, tmp_path = tools

    def fail_sink(path, payload):
        raise OSError("sink unavailable")

    monkeypatch.setattr(service_account_tools, "_atomic_sink", fail_sink)
    with pytest.raises(Exception, match="sink unavailable"):
        await fn(mcp, "create_service_account")("file", "failed-token", approved=True)
    sdk.service_accounts.create.assert_awaited_once()
    ledger = (tmp_path / "ledger.jsonl").read_text()
    assert '"resource_id":"sa-1"' in ledger
    assert not (tmp_path / "failed-token").exists()


async def test_file_sink_rejects_symlink_parent_before_creation(tools):
    mcp, sdk, tmp_path = tools
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(Exception, match="symlink"):
        await fn(mcp, "create_service_account")(
            "file", str(link / "token"), approved=True
        )
    sdk.service_accounts.create.assert_not_awaited()


async def test_create_sinks_token_directly_to_named_secret(tools):
    mcp, sdk, _ = tools
    result = await fn(mcp, "create_service_account")(
        "secret", "robot-token", approved=True
    )
    assert "one-time-private-token" not in repr(result)
    assert result["destination"] == {
        "type": "secret",
        "key": "robot-token",
        "context": {"cluster": "c", "org": "o", "project": "p"},
    }
    sdk.secrets.add.assert_awaited_once_with(
        "robot-token",
        b"one-time-private-token",
        cluster_name="c",
        org_name="o",
        project_name="p",
    )
    sdk.secrets.get.assert_not_awaited()


async def test_secret_collision_preflight_blocks_creation(tools):
    mcp, sdk, _ = tools
    existing = SimpleNamespace(
        key="robot-token", cluster_name="c", org_name="o", project_name="p"
    )
    sdk.secrets.list = lambda **kwargs: iterator([existing])
    with pytest.raises(Exception, match="already exists"):
        await fn(mcp, "create_service_account")("secret", "robot-token", approved=True)
    sdk.service_accounts.create.assert_not_awaited()


async def test_list_get_context_and_exact_delete(tools):
    mcp, sdk, _ = tools
    listed = await fn(mcp, "list_service_accounts")(limit=1)
    assert listed["truncated"] is True
    assert listed["context"] == {"cluster": "c", "org": "o", "project": "p"}
    got = await fn(mcp, "get_service_account")("sa-1")
    assert got["account"]["id"] == "sa-1"
    deleted = await fn(mcp, "delete_service_account")("sa-1", approved=True)
    assert deleted["id"] == "sa-1"
    sdk.service_accounts.rm.assert_awaited_once_with("sa-1")
    with pytest.raises(Exception, match="immutable"):
        await fn(mcp, "delete_service_account")("robot", approved=True)


async def test_every_write_requires_policy_and_approval(tools, monkeypatch):
    mcp, sdk, _ = tools
    with pytest.raises(PermissionError, match="approved=true"):
        await fn(mcp, "create_service_account")("file", "token")
    with pytest.raises(PermissionError, match="approved=true"):
        await fn(mcp, "delete_service_account")("sa-1")
    monkeypatch.setenv("APOLO_MCP_ENABLE_HIGH_RISK", "false")
    with pytest.raises(PermissionError, match="server policy"):
        await fn(mcp, "delete_service_account")("sa-1", approved=True)
    sdk.service_accounts.rm.assert_not_awaited()


async def test_ledger_owned_cleanup_and_annotations(tools):
    mcp, sdk, _ = tools
    await fn(mcp, "create_service_account")("file", "token", approved=True)
    result = await fn(mcp, "delete_service_account")("sa-1", automatic_cleanup=True)
    assert result["automatic_cleanup"] is True
    registered = {item.name: item for item in await mcp.list_tools()}
    assert registered["list_service_accounts"].annotations.readOnlyHint is True
    assert registered["delete_service_account"].annotations.destructiveHint is True
