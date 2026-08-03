from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.server import MCPServer

from apolo_mcp._client import reset_client_provider, set_client_provider
from apolo_mcp.errors import ApoloToolError
from apolo_mcp.tools.admin import register


class Provider:
    def __init__(self, sdk):
        self.sdk = sdk

    @asynccontextmanager
    async def client(self):
        yield self.sdk


def quota(value=3):
    return SimpleNamespace(total_running_jobs=value)


def balance():
    return SimpleNamespace(credits=Decimal("10.5"), spent_credits=Decimal("2"))


def info():
    return SimpleNamespace(
        email="alice@example.test",
        first_name="Alice",
        last_name="User",
        created_at=None,
    )


@pytest.fixture
def tools():
    config = SimpleNamespace(
        username="admin@example.test",
        cluster_name="alpha",
        org_name="team",
        project_name="default",
        clusters={"alpha": SimpleNamespace(orgs={"team": object()})},
        projects={
            "default": SimpleNamespace(
                cluster_name="alpha", org_name="team", name="default"
            )
        },
    )
    cluster = SimpleNamespace(
        name="alpha",
        default_credits=Decimal("100"),
        default_quota=quota(),
        default_role="user",
        maintenance=False,
    )
    cluster_user = SimpleNamespace(
        cluster_name="alpha",
        org_name="team",
        user_name="alice",
        role="user",
        quota=quota(),
        balance=balance(),
        user_info=info(),
    )
    org = SimpleNamespace(
        name="team", balance=balance(), user_default_credits=Decimal("5")
    )
    org_user = SimpleNamespace(
        org_name="team",
        user_name="alice",
        role="user",
        balance=balance(),
        user_info=info(),
    )
    org_cluster = SimpleNamespace(
        org_name="team",
        cluster_name="alpha",
        balance=balance(),
        quota=quota(),
        default_credits=Decimal("5"),
        default_quota=quota(2),
        default_role="user",
        storage_size=1024,
        maintenance=False,
    )
    project = SimpleNamespace(
        name="default",
        cluster_name="alpha",
        org_name="team",
        is_default=True,
        default_role="writer",
        has_virtual_kube=False,
    )
    project_user = SimpleNamespace(
        user_name="alice",
        cluster_name="alpha",
        org_name="team",
        project_name="default",
        role="writer",
        user_info=info(),
    )
    admin = SimpleNamespace(
        list_clusters=AsyncMock(return_value=[cluster]),
        list_cluster_users=AsyncMock(return_value=[cluster_user]),
        list_orgs=AsyncMock(return_value=[org]),
        list_org_users=AsyncMock(return_value=[org_user]),
        list_org_clusters=AsyncMock(return_value=[org_cluster]),
        get_org_cluster=AsyncMock(return_value=org_cluster),
        list_projects=AsyncMock(return_value=[project]),
        list_project_users=AsyncMock(return_value=[project_user]),
        get_cluster_user=AsyncMock(return_value=cluster_user),
        get_org_user=AsyncMock(return_value=org_user),
    )
    sdk = SimpleNamespace(config=config, _admin=admin)
    token = set_client_provider(Provider(sdk))
    mcp = MCPServer("admin-test")
    register(mcp)
    try:
        yield mcp, sdk
    finally:
        reset_client_provider(token)


def fn(mcp, name):
    return mcp._tool_manager._tools[name].fn


async def test_admin_reads_follow_cli_admin_facade_and_serialize_safely(tools):
    mcp, sdk = tools
    assert (await fn(mcp, "list_admin_clusters")())["items"][0]["name"] == "alpha"
    cluster_users = await fn(mcp, "list_admin_cluster_users")("alpha", "team", True)
    assert cluster_users["items"][0]["user_info"]["email"] == "alice@example.test"
    assert (await fn(mcp, "list_admin_orgs")())["items"][0]["name"] == "team"
    assert (await fn(mcp, "list_admin_org_users")("team"))["items"][0][
        "username"
    ] == "alice"
    assert (await fn(mcp, "list_admin_cluster_orgs")("alpha"))["items"][0][
        "storage_size"
    ] == 1024
    assert (await fn(mcp, "get_admin_org_cluster_quota")("alpha", "team"))[
        "org_cluster"
    ]["quota"] == {"total_running_jobs": 3}
    assert (await fn(mcp, "list_admin_projects")("alpha", "team"))["items"][0][
        "name"
    ] == "default"
    assert (await fn(mcp, "list_admin_project_users")("alpha", "default", "team"))[
        "items"
    ][0]["role"] == "writer"
    user_quota = await fn(mcp, "get_admin_user_quota")("alpha", "team", "alice")
    assert user_quota["balance"] == {"credits": "10.5", "spent_credits": "2"}

    sdk._admin.list_cluster_users.assert_awaited_once_with(
        cluster_name="alpha", with_user_info=True, org_name="team"
    )
    sdk._admin.list_project_users.assert_awaited_once_with(
        project_name="default",
        cluster_name="alpha",
        org_name="team",
        with_user_info=True,
    )


async def test_all_admin_tools_are_read_only_and_bounded(tools):
    mcp, _ = tools
    for tool in mcp._tool_manager._tools.values():
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
    with pytest.raises(ApoloToolError, match="limit"):
        await fn(mcp, "list_admin_clusters")(1001)
