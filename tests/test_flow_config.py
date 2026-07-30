from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from apolo_mcp.tools import flow_config


FLOW_SCHEMA = {
    "$defs": {
        "LiveFlow": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "kind": {"const": "live"},
                "jobs": {"type": "object"},
            },
            "required": ["kind", "jobs"],
        },
        "BatchFlow": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "kind": {"const": "batch"},
                "tasks": {"type": "array"},
            },
            "required": ["kind", "tasks"],
        },
        "Job": {"type": "object"},
        "Task": {"type": "object"},
    },
    "oneOf": [
        {"$ref": "#/$defs/LiveFlow"},
        {"$ref": "#/$defs/BatchFlow"},
    ],
}
PROJECT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"id": {"type": "string"}},
    "required": ["id"],
    "$defs": {"ProjectDefaults": {"type": "object"}},
}


class Provider:
    async def get(self, config_type):
        document = PROJECT_SCHEMA if config_type == "project" else FLOW_SCHEMA
        filename = (
            "project-schema.json" if config_type == "project" else "flow-schema.json"
        )
        return flow_config.SchemaResource(
            document=document,
            url=f"https://schemas.example/v26.7.2/{filename}",
            flow_version="26.7.2",
            sha256="abc123",
        )


@pytest.fixture()
def tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "managed")
    monkeypatch.setenv("APOLO_MCP_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    token = flow_config.set_flow_schema_provider(Provider())
    mcp = FastMCP("flow-config-test")
    flow_config.register(mcp)
    try:
        yield mcp._tool_manager._tools, workspace
    finally:
        flow_config.reset_flow_schema_provider(token)


def fn(tools, name):
    return tools[0][name].fn


async def test_schema_exploration_is_bounded_and_versioned(tools) -> None:
    root = await fn(tools, "flow_config_schema")("live")
    assert root["definition"] == "LiveFlow"
    assert root["schema"]["properties"]["kind"] == {"const": "live"}
    assert root["available_definitions"] == ["BatchFlow", "Job", "LiveFlow", "Task"]
    assert root["source"]["apolo_flow_version"] == "26.7.2"

    job = await fn(tools, "flow_config_schema")("live", "Job")
    assert job["schema"] == {"type": "object"}

    project = await fn(tools, "flow_config_schema")("project")
    assert "$defs" not in project["schema"]
    assert project["available_definitions"] == ["ProjectDefaults"]


@pytest.mark.parametrize(
    ("config_type", "batch_name", "config", "filename"),
    [
        ("live", None, {"kind": "live", "jobs": {}}, "live.yml"),
        ("batch", "train", {"kind": "batch", "tasks": []}, "train.yml"),
        ("project", None, {"id": "demo"}, "project.yml"),
    ],
)
async def test_write_creates_schema_annotated_validated_config(
    tools, config_type, batch_name, config, filename
) -> None:
    registered, workspace = tools
    result = await fn(tools, "flow_config_write")(
        str(workspace), config_type, config, batch_name
    )
    path = workspace / ".apolo" / filename
    assert result["path"] == str(path)
    assert result["valid"] is True
    assert path.read_text().startswith(
        "# yaml-language-server: $schema=https://schemas.example/v26.7.2/"
    )
    assert path.stat().st_mode & 0o777 == 0o600

    validated = await registered["flow_config_validate"].fn(
        str(workspace), config_type, batch_name
    )
    assert validated["valid"] is True
    assert validated["errors"] == []


async def test_invalid_or_existing_config_is_never_overwritten(tools) -> None:
    _, workspace = tools
    with pytest.raises(ValueError, match="schema validation"):
        await fn(tools, "flow_config_write")(str(workspace), "live", {"kind": "live"})
    assert not (workspace / ".apolo" / "live.yml").exists()

    await fn(tools, "flow_config_write")(
        str(workspace), "live", {"kind": "live", "jobs": {}}
    )
    with pytest.raises(FileExistsError, match="must not already exist"):
        await fn(tools, "flow_config_write")(
            str(workspace), "live", {"kind": "live", "jobs": {}}
        )


async def test_validation_returns_structured_errors_without_echoing_secrets(
    tools,
) -> None:
    _, workspace = tools
    directory = workspace / ".apolo"
    directory.mkdir()
    (directory / "live.yml").write_text("kind: batch\ntasks: []\n")
    result = await fn(tools, "flow_config_validate")(str(workspace), "live")
    assert result["valid"] is False
    assert result["errors"]

    (directory / "live.yml").write_text(
        "kind: live\njobs: {}\napi_token: plain-text-value\n"
    )
    with pytest.raises(ValueError, match="secret value") as info:
        await fn(tools, "flow_config_validate")(str(workspace), "live")
    assert "plain-text-value" not in str(info.value)


async def test_write_obeys_policy_and_rejects_unsafe_batch_names(
    tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, workspace = tools
    with pytest.raises(ValueError, match="batch_name"):
        await fn(tools, "flow_config_write")(
            str(workspace),
            "batch",
            {"kind": "batch", "tasks": []},
            "../escape",
        )

    from apolo_mcp.policy import _reset_policy_for_tests

    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "read-only")
    _reset_policy_for_tests()
    with pytest.raises(PermissionError, match="read-only"):
        await fn(tools, "flow_config_write")(str(workspace), "project", {"id": "demo"})


def test_schema_url_is_pinned_to_installed_release(monkeypatch) -> None:
    monkeypatch.setattr(flow_config, "_installed_flow_version", lambda: "26.7.2")
    assert flow_config.flow_schema_url("live") == (
        "https://raw.githubusercontent.com/neuro-inc/neuro-flow/refs/tags/v26.7.2/"
        "src/apolo_flow/flow-schema.json"
    )
    assert flow_config.flow_schema_url("project").endswith(
        "/src/apolo_flow/project-schema.json"
    )
