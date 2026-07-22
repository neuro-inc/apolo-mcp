# mypy: disable-error-code="no-untyped-def"

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
def tools(monkeypatch):
    monkeypatch.setenv("APOLO_MCP_ENABLE_HIGH_RISK", "true")
    cfg = SimpleNamespace(
        cluster_name="alpha",
        org_name="team",
        project_name="default",
        clusters={"alpha": SimpleNamespace(orgs=["team"])},
        projects={
            "p": SimpleNamespace(cluster_name="alpha", org_name="team", name="default")
        },
    )
    parser = SimpleNamespace(
        remote_image=lambda value, **kwargs: image(
            value.rsplit("/", 1)[-1].split(":", 1)[0],
            value.rsplit(":", 1)[1] if ":" in value.rsplit("/", 1)[-1] else None,
        )
    )
    images = SimpleNamespace(
        list=AsyncMock(return_value=[image(), image("other", project="elsewhere")]),
        tags=AsyncMock(return_value=[image("model", "v1"), image("model", "v2")]),
        digest=AsyncMock(return_value="sha256:" + "a" * 64),
        tag_info=AsyncMock(return_value=apolo_sdk.Tag("v1", 123)),
        rm=AsyncMock(),
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
    short_tags = await fn(tools, "list_image_tags")("image:model", 1)
    assert short_tags["repository"]["repository"] == "model"
    inspected = await fn(tools, "inspect_image")("model", "v1")
    assert inspected["digest"] == "sha256:" + "a" * 64
    assert inspected["size_bytes"] == 123 and "layers" not in inspected


async def test_cross_context_policy_and_exact_digest(tools, monkeypatch):
    with pytest.raises(ApoloToolError, match="does not belong"):
        await fn(tools, "list_image_tags")("image://beta/team/default/model")
    with pytest.raises(PermissionError, match="approved=true"):
        await fn(tools, "remove_image")("model", "v1", "sha256:" + "a" * 64)
    with pytest.raises(ValueError, match="lowercase sha256"):
        await fn(tools, "remove_image")("model", "v1", "bad", True)
    with pytest.raises(ApoloToolError, match="exact image tag"):
        await fn(tools, "inspect_image")("model", "bad:tag")
    monkeypatch.setenv("APOLO_MCP_ENABLE_HIGH_RISK", "false")
    with pytest.raises(PermissionError, match="server policy"):
        await fn(tools, "remove_image")("model", "v1", "sha256:" + "a" * 64, True)


async def test_remove_revalidates_digest_and_annotations(tools):
    digest = "sha256:" + "a" * 64
    await fn(tools, "remove_image")("model", "v1", digest, True)
    tools[1].images.rm.assert_awaited_once()
    tools[1].images.digest.return_value = "sha256:" + "b" * 64
    with pytest.raises(ApoloToolError, match="no longer matches"):
        await fn(tools, "remove_image")("model", "v1", digest, True)
    assert tools[0]["inspect_image"].annotations.readOnlyHint is True
    assert tools[0]["remove_image"].annotations.destructiveHint is True
