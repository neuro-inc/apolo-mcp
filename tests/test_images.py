# mypy: disable-error-code="no-untyped-def"

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import apolo_sdk
import pytest
from mcp.server.fastmcp import FastMCP

from apolo_mcp._client import reset_client_provider, set_client_provider
from apolo_mcp.errors import ApoloToolError
from apolo_mcp.tools.images import register


class Provider:
    def __init__(self, sdk):
        self.sdk = sdk

    @asynccontextmanager
    async def client(self):
        yield self.sdk


def image(name="model", tag=None, project="default"):
    return apolo_sdk.RemoteImage.new_platform_image(
        name,
        "registry.example",
        cluster_name="alpha",
        org_name="team",
        project_name=project,
        tag=tag,
    )


@pytest.fixture()
def tools(monkeypatch, tmp_path):
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "full")
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
    remote_image = Mock(
        side_effect=lambda value, **kwargs: image(
            value.rsplit("/", 1)[-1].split(":", 1)[0],
            value.rsplit(":", 1)[1] if ":" in value.rsplit("/", 1)[-1] else None,
        )
    )
    parser = SimpleNamespace(
        local_image=lambda value: apolo_sdk.LocalImage(value),
        remote_image=remote_image,
    )
    images = SimpleNamespace(
        list=AsyncMock(return_value=[image(), image("other", project="elsewhere")]),
        tags=AsyncMock(return_value=[image("model", "v1"), image("model", "v2")]),
        digest=AsyncMock(return_value="sha256:" + "a" * 64),
        tag_info=AsyncMock(return_value=apolo_sdk.Tag("v1", 123)),
        rm=AsyncMock(),
        push=AsyncMock(return_value=image("model", "v1")),
        pull=AsyncMock(return_value=apolo_sdk.LocalImage("local:model")),
    )
    sdk = SimpleNamespace(config=cfg, parse=parser, images=images)
    token = set_client_provider(Provider(sdk))
    mcp = FastMCP("image-test")
    register(mcp)
    try:
        yield mcp._tool_manager._tools, sdk
    finally:
        reset_client_provider(token)


def fn(tools, name):
    return tools[0][name].fn


async def test_lists_are_bounded_filtered_and_inspection_has_no_layers(tools):
    repos = await fn(tools, "list_image_repositories")(1)
    assert len(repos["items"]) == 1 and repos["truncated"] is False
    tags = await fn(tools, "list_image_tags")("model", 1)
    assert tags["truncated"] is True
    assert tools[1].parse.remote_image.call_args.kwargs["tag_option"] is (
        apolo_sdk.TagOption.DENY
    )
    short_tags = await fn(tools, "list_image_tags")("image:model", 1)
    assert short_tags["repository"]["repository"] == "model"
    inspected = await fn(tools, "inspect_image")("model", "v1")
    assert tools[1].parse.remote_image.call_args.kwargs["tag_option"] is (
        apolo_sdk.TagOption.ALLOW
    )
    assert inspected["digest"] == "sha256:" + "a" * 64
    assert inspected["size_bytes"] == 123 and "layers" not in inspected


async def test_cross_context_policy_and_exact_tag(tools, monkeypatch):
    with pytest.raises(ApoloToolError, match="does not belong"):
        await fn(tools, "list_image_tags")("image://beta/team/default/model")
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "read-only")
    with pytest.raises(ApoloToolError, match="server policy"):
        await fn(tools, "remove_image_tag")("model", "v1")
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "full")
    with pytest.raises(ApoloToolError, match="exact image tag"):
        await fn(tools, "inspect_image")("model", "bad:tag")
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "read-only")
    with pytest.raises(ApoloToolError, match="server policy"):
        await fn(tools, "remove_image_tag")("model", "v1")


async def test_remove_passes_tag_not_digest_and_annotations(tools):
    await fn(tools, "remove_image_tag")("model", "v1")
    remote, reference = tools[1].images.rm.await_args.args
    assert remote.tag == "v1"
    assert reference == "v1"
    tools[1].images.digest.assert_not_awaited()
    assert tools[0]["inspect_image"].annotations.readOnlyHint is True
    assert tools[0]["remove_image_tag"].annotations.destructiveHint is True


async def test_push_and_pull_use_sdk_with_exact_context(tools, tmp_path, monkeypatch):
    monkeypatch.setenv("APOLO_MCP_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    pushed = await fn(tools, "push_image")("local:model", "model", "v1")
    assert pushed["image"]["project"] == "default"
    tools[1].images.push.assert_awaited_once()
    assert '"resource_type":"image"' in (tmp_path / "ledger.jsonl").read_text()

    pulled = await fn(tools, "pull_image")("model", "v1", "local:copy")
    assert pulled["local_image"] == "local:model"
    tools[1].images.pull.assert_awaited_once()
    assert str(tools[1].images.pull.await_args.args[1]) == "local:copy"

    await fn(tools, "push_image")("local:model", "model", "v1")
    assert tools[1].images.push.await_count == 2
    with pytest.raises(ValueError, match="timeout_seconds"):
        await fn(tools, "pull_image")("model", "v1", timeout_seconds=1801)
