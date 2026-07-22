import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import FastMCP
from yarl import URL

from apolo_mcp._client import reset_client_provider, set_client_provider
from apolo_mcp.tools.secrets import register


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


def secret(key="api"):
    return SimpleNamespace(
        key=key,
        owner="alice",
        cluster_name="c",
        org_name="o",
        project_name="p",
        uri=URL(f"secret://c/o/p/{key}"),
    )


@pytest.fixture
def tools(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("APOLO_MCP_ENABLE_HIGH_RISK", "true")
    monkeypatch.setenv("APOLO_MCP_ALLOWED_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("APOLO_MCP_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    secrets = MagicMock()
    secrets.list = lambda **kwargs: iterator([secret("one"), secret("two")])
    secrets.add = AsyncMock()
    secrets.get = AsyncMock(side_effect=AssertionError("must never retrieve values"))
    secrets.rm = AsyncMock()
    sdk = SimpleNamespace(config=config(), secrets=secrets)
    token = set_client_provider(Provider(sdk))
    mcp = FastMCP("secrets-test")
    register(mcp)
    try:
        yield mcp, sdk
    finally:
        reset_client_provider(token)


def fn(mcp, name):
    return mcp._tool_manager._tools[name].fn


async def test_list_is_bounded_metadata_only_and_context_explicit(tools):
    mcp, sdk = tools
    result = await fn(mcp, "list_secrets")(limit=1)
    assert result["truncated"] is True
    assert result["context"] == {"cluster": "c", "org": "o", "project": "p"}
    assert set(result["items"][0]) == {
        "key",
        "owner",
        "cluster",
        "org",
        "project",
        "uri",
    }
    sdk.secrets.get.assert_not_awaited()


async def test_create_from_env_never_serializes_value(tools, monkeypatch):
    mcp, sdk = tools
    monkeypatch.setenv("NAMED_SOURCE", "extremely-private-value")
    result = await fn(mcp, "create_secret_from_source")(
        "api", "env", "NAMED_SOURCE", approved=True
    )
    assert "extremely-private-value" not in repr(result)
    assert result["source"] == {"type": "env", "name": "NAMED_SOURCE"}
    assert sdk.secrets.add.await_args.args == ("api", b"extremely-private-value")
    assert sdk.secrets.add.await_args.kwargs == {
        "cluster_name": "c",
        "org_name": "o",
        "project_name": "p",
    }
    sdk.secrets.get.assert_not_awaited()


async def test_create_from_same_context_secret_is_internal_and_ledgered(tools):
    mcp, sdk = tools
    sdk.secrets.get.side_effect = None
    sdk.secrets.get.return_value = b"copied-private-value"
    result = await fn(mcp, "create_secret_from_source")(
        "copy", "secret", "source", approved=True
    )
    assert "copied-private-value" not in repr(result)
    sdk.secrets.get.assert_awaited_once_with(
        "source", cluster_name="c", org_name="o", project_name="p"
    )
    sdk.secrets.add.assert_awaited_once_with(
        "copy",
        b"copied-private-value",
        cluster_name="c",
        org_name="o",
        project_name="p",
    )
    ledger = Path(os.environ["APOLO_MCP_LEDGER_PATH"]).read_text()
    assert '"resource_type":"secret"' in ledger
    assert '"resource_id":"copy"' in ledger


async def test_creation_ledger_preflight_happens_before_sdk_write(
    tools, monkeypatch, tmp_path: Path
):
    mcp, sdk = tools
    monkeypatch.setenv("NAMED_SOURCE", "value")
    monkeypatch.setenv("APOLO_MCP_LEDGER_PATH", str(tmp_path))
    sdk.secrets.add.reset_mock()
    with pytest.raises(Exception):
        await fn(mcp, "create_secret_from_source")(
            "blocked", "env", "NAMED_SOURCE", approved=True
        )
    sdk.secrets.add.assert_not_awaited()


async def test_create_from_protected_file_and_rejects_open_permissions(
    tools, tmp_path: Path
):
    mcp, sdk = tools
    source = tmp_path / "source"
    source.write_bytes(b"file-private-value")
    source.chmod(0o600)
    result = await fn(mcp, "create_secret_from_source")(
        "file-key", "file", str(source), approved=True
    )
    assert "file-private-value" not in repr(result)
    assert sdk.secrets.add.await_args.args[1] == b"file-private-value"
    source.chmod(0o644)
    with pytest.raises(Exception, match="group or others"):
        await fn(mcp, "create_secret_from_source")(
            "bad", "file", str(source), approved=True
        )


async def test_every_write_requires_policy_and_approval(tools, monkeypatch):
    mcp, sdk = tools
    monkeypatch.setenv("NAMED_SOURCE", "value")
    with pytest.raises(PermissionError, match="approved=true"):
        await fn(mcp, "create_secret_from_source")("api", "env", "NAMED_SOURCE")
    with pytest.raises(PermissionError, match="approved=true"):
        await fn(mcp, "delete_secret")("api")
    monkeypatch.setenv("APOLO_MCP_ENABLE_HIGH_RISK", "false")
    with pytest.raises(PermissionError, match="server policy"):
        await fn(mcp, "delete_secret")("api", approved=True)
    sdk.secrets.rm.assert_not_awaited()


async def test_exact_delete_and_annotations(tools):
    mcp, sdk = tools
    result = await fn(mcp, "delete_secret")("api", approved=True)
    assert result["context"]["project"] == "p"
    sdk.secrets.rm.assert_awaited_once_with(
        "api", cluster_name="c", org_name="o", project_name="p"
    )
    with pytest.raises(ValueError, match="exact"):
        await fn(mcp, "delete_secret")("folder/api", approved=True)
    registered = {item.name: item for item in await mcp.list_tools()}
    assert registered["list_secrets"].annotations.readOnlyHint is True
    assert registered["delete_secret"].annotations.destructiveHint is True
