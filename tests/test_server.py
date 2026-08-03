from apolo_mcp.server import mcp


async def test_context_tools_are_registered_with_read_only_annotations() -> None:
    tools = {item.name: item for item in await mcp.list_tools()}
    expected = {
        "get_apolo_context",
        "list_clusters",
        "list_organizations",
        "list_projects",
        "list_presets",
        "resolve_resource_uri",
    }
    assert expected <= tools.keys()
    for name in expected:
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is True
        assert annotations.destructive_hint is False
        assert annotations.idempotent_hint is True


async def test_resource_type_schema_is_closed() -> None:
    tools = {item.name: item for item in await mcp.list_tools()}
    schema = tools["resolve_resource_uri"].input_schema
    assert schema["properties"]["resource_type"]["enum"] == [
        "storage",
        "image",
        "secret",
        "disk",
    ]


async def test_registered_tools_have_write_and_destructive_annotations() -> None:
    tools = {item.name: item for item in await mcp.list_tools()}
    assert tools["run_job"].annotations.read_only_hint is False
    assert tools["run_job"].annotations.destructive_hint is False
    assert tools["exec_job"].annotations.read_only_hint is False
    assert tools["exec_job"].annotations.destructive_hint is False
    assert tools["start_job_port_forward"].annotations.read_only_hint is False
    assert tools["list_job_port_forwards"].annotations.read_only_hint is True
    assert tools["stop_job_port_forward"].annotations.read_only_hint is False
    assert tools["kill_job"].annotations.read_only_hint is False
    assert tools["kill_job"].annotations.destructive_hint is True
    assert tools["kill_job"].annotations.idempotent_hint is True


async def test_reviewed_resource_tools_are_registered() -> None:
    tools = {item.name: item for item in await mcp.list_tools()}
    expected = {
        "list_storage",
        "list_disks",
        "list_image_repositories",
        "list_buckets",
        "list_secrets",
        "list_service_accounts",
        "list_bucket_credentials",
        "create_bucket_credentials",
        "export_bucket_credentials",
        "delete_bucket_credentials",
        "create_bucket_signed_url",
        "create_secret_from_source",
        "create_service_account",
    }
    assert expected <= tools.keys()
    assert tools["list_buckets"].annotations.read_only_hint is True
    assert tools["delete_bucket"].annotations.destructive_hint is True
    assert tools["create_service_account"].annotations.read_only_hint is False


async def test_admin_discovery_tools_are_registered_read_only() -> None:
    tools = {item.name: item for item in await mcp.list_tools()}
    expected = {
        "list_admin_clusters",
        "list_admin_cluster_users",
        "list_admin_orgs",
        "list_admin_org_users",
        "list_admin_cluster_orgs",
        "get_admin_org_cluster_quota",
        "list_admin_projects",
        "list_admin_project_users",
        "get_admin_user_quota",
    }
    assert expected <= tools.keys()
    for name in expected:
        assert tools[name].annotations.read_only_hint is True
        assert tools[name].annotations.destructive_hint is False


def test_essential_safety_instruction_is_early() -> None:
    first = mcp.instructions[:512]
    assert "Before any write" in first
    assert "authenticated username" in first
    assert "explicit context" in first
    assert "never change saved context" in first
    assert "Never request, return, or log tokens" in first
