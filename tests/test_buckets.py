import os
import stat
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import FastMCP
from yarl import URL

from apolo_mcp._client import reset_client_provider, set_client_provider
from apolo_mcp.errors import ApoloToolError
from apolo_mcp.tools.buckets import register


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


def bucket(id="bucket-1", name="data", public=False):
    return SimpleNamespace(
        id=id,
        name=name,
        owner="alice",
        provider=SimpleNamespace(value="aws"),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        imported=False,
        public=public,
        uri=URL(f"blob://c/o/p/{name or id}"),
        cluster_name="c",
        org_name="o",
        project_name="p",
    )


def blob(key="dir/item.txt", size=12):
    item = SimpleNamespace(
        key=key,
        name=key.rsplit("/", 1)[-1],
        size=size,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        modified_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        uri=URL(f"blob://c/o/p/data/{key}"),
    )
    item.is_file = lambda: True
    item.is_dir = lambda: False
    return item


@pytest.fixture
def tools(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "full")
    monkeypatch.setenv("APOLO_MCP_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("APOLO_MCP_ALLOWED_WORKSPACE", str(tmp_path))
    buckets = MagicMock()
    buckets.list = lambda **kwargs: iterator([bucket(), bucket("bucket-2", "two")])
    buckets.get = AsyncMock(return_value=bucket())
    buckets.create = AsyncMock(return_value=bucket())
    buckets.import_external = AsyncMock(return_value=bucket())
    buckets.set_public_access = AsyncMock(return_value=bucket(public=True))
    buckets.head_blob = AsyncMock(return_value=blob())
    buckets.list_blobs = lambda *args, **kwargs: iterator([blob("one"), blob("two")])
    buckets.get_disk_usage = lambda *args, **kwargs: iterator(
        [SimpleNamespace(total_bytes=12, object_count=1)]
    )
    buckets.make_signed_url = AsyncMock(
        return_value=URL("https://objects.invalid/x?temporary-signature=yes")
    )

    async def download_file(src, dst, **kwargs):
        Path(dst.path).write_bytes(b"x" * 12)

    buckets.upload_file = AsyncMock()
    buckets.download_file = AsyncMock(side_effect=download_file)
    buckets.delete_blob = AsyncMock()
    buckets.rm = AsyncMock()
    secrets = SimpleNamespace(get=AsyncMock(return_value=b'{"access_key":"private"}'))
    sdk = SimpleNamespace(config=config(), buckets=buckets, secrets=secrets)
    token = set_client_provider(Provider(sdk))
    mcp = FastMCP("buckets-test")
    register(mcp)
    try:
        yield mcp, sdk, tmp_path
    finally:
        reset_client_provider(token)


def fn(mcp, name):
    return mcp._tool_manager._tools[name].fn


async def test_bounded_lists_context_and_safe_metadata(tools):
    mcp, sdk, _ = tools
    result = await fn(mcp, "list_buckets")(limit=1)
    assert result["truncated"] is True
    assert result["context"] == {"cluster": "c", "org": "o", "project": "p"}
    blob_result = await fn(mcp, "list_bucket_blobs")("bucket-1", limit=1)
    assert blob_result["truncated"] is True
    assert blob_result["items"][0]["size_bytes"] == 12
    assert sdk.buckets.list_blobs.__name__ == "<lambda>"
    with pytest.raises(ValueError, match="limit"):
        await fn(mcp, "list_buckets")(limit=101)


async def test_create_preflights_and_records_ledger(tools):
    mcp, sdk, tmp_path = tools
    result = await fn(mcp, "create_bucket")("data")
    assert result["bucket"]["id"] == "bucket-1"
    sdk.buckets.create.assert_awaited_once_with(
        name="data", cluster_name="c", org_name="o", project_name="p"
    )
    ledger = (tmp_path / "ledger.jsonl").read_text()
    assert '"resource_type":"bucket"' in ledger
    assert '"resource_id":"bucket-1"' in ledger


async def test_import_reads_credentials_internally_and_never_returns_them(
    tools, monkeypatch
):
    mcp, sdk, tmp_path = tools
    monkeypatch.setenv(
        "BUCKET_CREDS", '{"access_key":"private-access","region":"west"}'
    )
    result = await fn(mcp, "import_external_bucket")(
        "aws", "external", "env", "BUCKET_CREDS"
    )
    assert "private-access" not in repr(result)
    args = sdk.buckets.import_external.await_args.args
    assert args[1] == "external"
    assert args[2] == {"access_key": "private-access", "region": "west"}
    ledger = (tmp_path / "ledger.jsonl").read_text()
    assert '"operation":"import_external_bucket"' in ledger


async def test_all_mutations_use_server_policy_without_approval_parameter(
    tools, monkeypatch
):
    mcp, sdk, _ = tools
    for name in (
        "create_bucket",
        "import_external_bucket",
        "set_bucket_public_access",
        "create_bucket_signed_url",
        "upload_bucket_file",
        "download_bucket_file",
        "delete_bucket_blob",
        "delete_bucket",
    ):
        assert "approved" not in mcp._tool_manager._tools[name].parameters["properties"]
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "read-only")
    with pytest.raises(ApoloToolError, match="server policy"):
        await fn(mcp, "delete_bucket")("bucket-1")
    sdk.buckets.rm.assert_not_awaited()


async def test_signed_url_is_short_lived_sink_only_and_preflighted(tools):
    mcp, sdk, tmp_path = tools
    result = await fn(mcp, "create_bucket_signed_url")(
        "bucket-1",
        "dir/item.txt",
        "signed/url.txt",
        expires_in_seconds=30,
    )
    assert result["expires_in_seconds"] == 30
    assert "url" not in result
    assert "temporary-signature" not in repr(result)
    target = tmp_path / "signed/url.txt"
    assert "temporary-signature" in target.read_text()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    sdk.buckets.head_blob.assert_awaited_once()
    sdk.buckets.make_signed_url.assert_awaited_once()
    with pytest.raises(ValueError, match="expires_in_seconds"):
        await fn(mcp, "create_bucket_signed_url")(
            "bucket-1", "one", "too-long", expires_in_seconds=3601
        )


async def test_file_transfers_are_workspace_bounded_and_never_return_bytes(tools):
    mcp, sdk, tmp_path = tools
    source = tmp_path / "source.bin"
    source.write_bytes(b"x" * 12)
    uploaded = await fn(mcp, "upload_bucket_file")(
        str(source), "bucket-1", "dir/item.txt", max_bytes=12
    )
    assert uploaded["size_bytes"] == 12
    assert "b'" not in repr(uploaded)
    sdk.buckets.upload_file.assert_awaited_once()
    downloaded = await fn(mcp, "download_bucket_file")(
        "bucket-1", "dir/item.txt", "download.bin", max_bytes=12
    )
    assert downloaded["size_bytes"] == 12
    assert (tmp_path / "download.bin").read_bytes() == b"x" * 12
    assert "b'" not in repr(downloaded)
    with pytest.raises(PermissionError, match="allowed workspace"):
        await fn(mcp, "upload_bucket_file")("/etc/hosts", "bucket-1", "hosts")
    with pytest.raises(ValueError, match="max_bytes"):
        await fn(mcp, "upload_bucket_file")(
            str(source), "bucket-1", "large", max_bytes=1
        )


async def test_exact_deletes_and_ledger_owned_cleanup(tools):
    mcp, sdk, _ = tools
    await fn(mcp, "create_bucket")("data")
    await fn(mcp, "delete_bucket_blob")("bucket-1", "dir/item.txt")
    sdk.buckets.delete_blob.assert_awaited_once_with(
        "bucket-1",
        "dir/item.txt",
        cluster_name="c",
        org_name="o",
        project_name="p",
    )
    result = await fn(mcp, "delete_bucket")("bucket-1")
    assert result["id"] == "bucket-1"
    assert '"action":"deleted"' in Path(os.environ["APOLO_MCP_LEDGER_PATH"]).read_text()
    sdk.buckets.rm.assert_awaited_once_with(
        "bucket-1", cluster_name="c", org_name="o", project_name="p"
    )
    with pytest.raises(ValueError, match="exact"):
        await fn(mcp, "delete_bucket_blob")("bucket-1", "directory/")


async def test_usage_truthfully_reports_bound_and_annotations(tools):
    mcp, _, _ = tools
    result = await fn(mcp, "get_bucket_disk_usage")(
        "bucket-1", max_objects=10, timeout_seconds=1
    )
    assert result["complete"] is True
    assert result["object_count"] == 1
    registered = {item.name: item for item in await mcp.list_tools()}
    assert registered["list_buckets"].annotations.readOnlyHint is True
    assert registered["delete_bucket_blob"].annotations.destructiveHint is True
    assert registered["create_bucket"].annotations.readOnlyHint is False
