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
        assert annotations.readOnlyHint is True
        assert annotations.destructiveHint is False
        assert annotations.idempotentHint is True


async def test_resource_type_schema_is_closed() -> None:
    tools = {item.name: item for item in await mcp.list_tools()}
    schema = tools["resolve_resource_uri"].inputSchema
    assert schema["properties"]["resource_type"]["enum"] == [
        "storage",
        "image",
        "secret",
        "disk",
    ]


async def test_registered_tools_have_write_and_destructive_annotations() -> None:
    tools = {item.name: item for item in await mcp.list_tools()}
    assert tools["run_job"].annotations.readOnlyHint is False
    assert tools["run_job"].annotations.destructiveHint is False
    assert tools["kill_job"].annotations.readOnlyHint is False
    assert tools["kill_job"].annotations.destructiveHint is True
    assert tools["kill_job"].annotations.idempotentHint is True


async def test_reviewed_resource_tools_are_registered() -> None:
    tools = {item.name: item for item in await mcp.list_tools()}
    expected = {
        "list_storage",
        "list_disks",
        "list_image_repositories",
        "list_buckets",
        "list_secrets",
        "list_service_accounts",
        "create_bucket_signed_url",
        "create_secret_from_source",
        "create_service_account",
    }
    assert expected <= tools.keys()
    assert tools["list_buckets"].annotations.readOnlyHint is True
    assert tools["delete_bucket"].annotations.destructiveHint is True
    assert tools["create_service_account"].annotations.readOnlyHint is False


def test_essential_safety_instruction_is_early() -> None:
    first = mcp.instructions[:512]
    assert "Before any write" in first
    assert "explicit context" in first
    assert "never change saved context" in first
    assert "Never request, return, or log tokens" in first
