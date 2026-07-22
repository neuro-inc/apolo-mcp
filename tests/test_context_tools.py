from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from apolo_mcp._client import reset_client_provider, set_client_provider
from apolo_mcp.server import mcp


class FakeProvider:
    def __init__(self, sdk):
        self.sdk = sdk

    @asynccontextmanager
    async def client(self):
        yield self.sdk


@pytest.fixture()
def fake_sdk(tmp_path: Path):
    preset = SimpleNamespace(
        cpu=2.0,
        memory=8 * 2**30,
        credits_per_hour=Decimal("1.25"),
        nvidia_gpu=SimpleNamespace(count=1, model="A10", memory=24 * 2**30),
        amd_gpu=None,
        intel_gpu=None,
        tpu=None,
        scheduler_enabled=True,
        preemptible_node=False,
        resource_pool_names=("gpu",),
    )
    clusters = {
        "alpha": SimpleNamespace(
            name="alpha", orgs=["team", "lab"], presets={"gpu-small": preset}
        ),
        "beta": SimpleNamespace(name="beta", orgs=["other"], presets={}),
    }
    projects = {
        "p1": SimpleNamespace(
            cluster_name="alpha", org_name="team", name="default", role="owner"
        ),
        "p2": SimpleNamespace(
            cluster_name="beta", org_name="other", name="research", role="viewer"
        ),
    }
    config = SimpleNamespace(
        username="user@example.test",
        cluster_name="alpha",
        org_name="team",
        project_name="default",
        clusters=clusters,
        projects=projects,
        path=tmp_path,
    )
    return SimpleNamespace(config=config)


@pytest.fixture(autouse=True)
def provider(fake_sdk):
    token = set_client_provider(FakeProvider(fake_sdk))
    try:
        yield
    finally:
        reset_client_provider(token)


def tool(name: str):
    return mcp._tool_manager._tools[name].fn


async def test_get_context_contains_metadata_but_no_credentials() -> None:
    result = await tool("get_apolo_context")()
    assert result["cluster"] == "alpha"
    assert result["username"] == "user@example.test"
    assert result["versions"]["server"] is None
    assert "no public server-version accessor" in result["versions"]["server_note"]
    serialized = repr(result).lower()
    assert "token" not in serialized
    assert "cookie" not in serialized


async def test_discovery_filters_and_bounds() -> None:
    clusters = await tool("list_clusters")(1)
    assert clusters["items"] == [{"name": "alpha", "selected": True}]
    assert clusters["truncated"] is True
    orgs = await tool("list_organizations")("alpha", 1)
    assert len(orgs["items"]) == 1
    assert orgs["truncated"] is True
    projects = await tool("list_projects")("beta", "other", 10)
    assert projects["items"] == [{"name": "research", "role": "viewer"}]
    presets = await tool("list_presets")("alpha", 10)
    assert presets["items"][0]["accelerator"]["model"] == "A10"
    assert presets["items"][0]["credits_per_hour"] == "1.25"


async def test_limits_are_strict() -> None:
    with pytest.raises(ValueError, match="between 1 and 100"):
        await tool("list_clusters")(101)


async def test_resolve_uri_uses_explicit_context_without_switching() -> None:
    result = await tool("resolve_resource_uri")(
        "storage:reports/final.md", "storage", "beta", "other", "research"
    )
    assert result == {
        "uri": "storage://beta/other/research/reports/final.md",
        "context": {"cluster": "beta", "org": "other", "project": "research"},
    }


async def test_resolve_uri_rejects_credentials() -> None:
    with pytest.raises(RuntimeError) as info:
        await tool("resolve_resource_uri")(
            "https://user:pass@example.test/value", "storage"
        )
    assert "user:pass" not in str(info.value)
