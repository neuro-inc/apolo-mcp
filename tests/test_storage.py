# mypy: disable-error-code="no-untyped-def"

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import apolo_sdk
import pytest
from mcp.server.fastmcp import FastMCP
from yarl import URL

from apolo_mcp._client import reset_client_provider, set_client_provider
from apolo_mcp.errors import ApoloToolError
from apolo_mcp.tools.storage import MAX_TEXT_BYTES, register


class Provider:
    def __init__(self, sdk):
        self.sdk = sdk

    @asynccontextmanager
    async def client(self):
        yield self.sdk


class Storage:
    def __init__(self):
        self.create = AsyncMock()
        self.mkdir = AsyncMock()
        self.rm = AsyncMock()
        self.stat = AsyncMock(return_value=self.status("file", 4))
        self.entries = [self.status("a", 1), self.status("b", 2)]
        self.data = b"hello"

    @staticmethod
    def status(name, size):
        return apolo_sdk.FileStatus(
            path=name,
            size=size,
            type=apolo_sdk.FileStatusType.FILE,
            modification_time=1,
            permission=apolo_sdk.Action.READ,
            uri=URL(f"storage://alpha/team/default/{name}"),
        )

    async def list(self, uri):
        for item in self.entries:
            yield item

    async def open(self, uri, offset=0, size=None):
        yield self.data[:size]


def config():
    project = SimpleNamespace(cluster_name="alpha", org_name="team", name="default")
    return SimpleNamespace(
        cluster_name="alpha",
        org_name="team",
        project_name="default",
        clusters={"alpha": SimpleNamespace(orgs=["team"])},
        projects={"p": project},
    )


@pytest.fixture()
def tools(monkeypatch, tmp_path):
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "full")
    monkeypatch.setenv("APOLO_MCP_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    sdk = SimpleNamespace(config=config(), storage=Storage())
    token = set_client_provider(Provider(sdk))
    mcp = FastMCP("storage-test")
    register(mcp)
    try:
        yield mcp._tool_manager._tools, sdk
    finally:
        reset_client_provider(token)


def fn(tools, name):
    return tools[0][name].fn


async def test_bounded_list_read_and_serialization(tools):
    listed = await fn(tools, "list_storage")("data", 1)
    assert listed["truncated"] is True
    assert listed["items"][0]["size_bytes"] == 1
    tools[1].storage.data = b"hello!"
    read = await fn(tools, "read_text")("data/file", 5)
    assert read["text"] == "hello" and read["truncated"] is True
    assert read["context"]["cluster"] == "alpha"
    tools[1].storage.data = b"token=unsafe https://user:pass@example.test/path"
    read = await fn(tools, "read_text")("data/file", 100)
    assert "unsafe" not in read["text"] and "user:pass" not in read["text"]
    assert read["redacted"] is True and read["bytes"] <= 100


async def test_write_caps_policy_and_cross_context(tools, monkeypatch):
    with pytest.raises(ValueError, match="must not exceed"):
        await fn(tools, "write_text")("x", "x" * (MAX_TEXT_BYTES + 1))
    with pytest.raises(ApoloToolError, match="exact resolved context"):
        await fn(tools, "stat_storage")("storage://beta/team/default/file")
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "read-only")
    with pytest.raises(PermissionError, match="server policy"):
        await fn(tools, "write_text")("x", "safe")


async def test_storage_writes_have_no_model_supplied_approval(tools):
    await fn(tools, "write_text")("x", "safe")
    await fn(tools, "make_directory")("dir", parents=True, exist_ok=True)
    assert "approved" not in tools[0]["write_text"].parameters["properties"]


async def test_delete_rejects_root_and_passes_recursive_flag(tools):
    with pytest.raises(ApoloToolError, match="root cannot"):
        await fn(tools, "delete_storage_path")("")
    await fn(tools, "delete_storage_path")("safe", recursive=True)
    assert tools[1].storage.rm.await_args.kwargs == {"recursive": True}


async def test_storage_annotations(tools):
    assert tools[0]["read_text"].annotations.readOnlyHint is True
    assert tools[0]["write_text"].annotations.readOnlyHint is False
    assert tools[0]["delete_storage_path"].annotations.destructiveHint is True
