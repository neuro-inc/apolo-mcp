import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import apolo_sdk
import pytest
from mcp.server.fastmcp import FastMCP

from apolo_mcp import app_plans
from apolo_mcp.tools.apps import register


@asynccontextmanager
async def iterator(items):
    async def generate():
        for item in items:
            yield item

    yield generate()


def config() -> SimpleNamespace:
    project = SimpleNamespace(cluster_name="c", org_name="o", name="p")
    cluster = SimpleNamespace(orgs={"o": object()})
    return SimpleNamespace(
        cluster_name="c",
        org_name="o",
        project_name="p",
        clusters={"c": cluster},
        projects={"p": project},
    )


def app(state: str = "healthy") -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id="app-1",
        name="app",
        display_name="App",
        template_name="service-deployment",
        template_version="2.4",
        state=state,
        endpoints=["https://service.example"],
        namespace="ns",
        cluster_name="c",
        org_name="o",
        project_name="p",
        created_at=now,
        updated_at=now,
    )


def revision(number: int) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        revision_number=number,
        creator="user",
        comment=None,
        created_at=now,
        end_at=None,
    )


def template() -> SimpleNamespace:
    return SimpleNamespace(
        name="service-deployment",
        version="2.4",
        title="Service",
        short_description="Deploy a service",
        description="",
        tags=["service"],
        input={
            "type": "object",
            "required": ["image", "preset"],
            "properties": {
                "image": {"type": "string", "minLength": 1},
                "preset": {"type": "string"},
                "command": {"type": "string"},
                "env": {"type": "object"},
                "storage": {"type": "array"},
                "secret_env": {"type": "array"},
                "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                "ingress": {"type": "object"},
                "auth": {"type": "boolean"},
                "replicas": {"type": "integer", "minimum": 1},
                "readiness_probe": {"type": "object"},
            },
        },
    )


@pytest.fixture
def tools(mock_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(app_plans.PLAN_ROOT_ENV, str(tmp_path / "plans"))
    monkeypatch.setenv("APOLO_MCP_ENABLE_HIGH_RISK", "true")
    monkeypatch.setenv("APOLO_MCP_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    mock_client.config = config()
    mock_client.apps.get_template = AsyncMock(return_value=template())
    mock_client.apps.get = AsyncMock(return_value=app())
    mock_client.apps.get_input = AsyncMock(
        return_value={
            "template_name": "service-deployment",
            "template_version": "2.4",
            "input": {"image": "repo/image:1", "preset": "cpu-small"},
        }
    )
    mock_client.apps.get_revisions = AsyncMock(return_value=[revision(3)])
    mock_client.apps.install = AsyncMock(return_value=app())
    mock_client.apps.configure = AsyncMock(return_value=app())
    mock_client.apps.rollback = AsyncMock(return_value=app())
    mock_client.apps.uninstall = AsyncMock(return_value=None)
    mock_client.apps.list_templates = lambda **kwargs: iterator([template()])
    mock_client.apps.list_template_versions = lambda **kwargs: iterator([template()])
    mock_client.apps.list = lambda **kwargs: iterator([app()])
    mcp = FastMCP("apps-test")
    register(mcp)
    return mcp, mock_client


def fn(mcp: FastMCP, name: str):
    tool = mcp._tool_manager.get_tool(name)
    assert tool is not None
    return tool.fn


async def test_annotations_and_read_bounds(tools) -> None:
    mcp, _ = tools
    registered = {item.name: item for item in await mcp.list_tools()}
    for name in (
        "list_app_templates",
        "list_app_template_versions",
        "get_app_template",
        "list_apps",
        "get_app",
        "wait_for_app",
        "get_app_logs",
        "get_app_events",
        "get_app_output",
        "get_app_input",
        "list_app_revisions",
    ):
        assert registered[name].annotations.readOnlyHint is True
    assert registered["rollback_app"].annotations.destructiveHint is True
    assert registered["uninstall_app"].annotations.destructiveHint is True
    with pytest.raises(ValueError, match="limit"):
        await fn(mcp, "list_apps")(limit=101)
    with pytest.raises(ValueError, match="timeout_seconds"):
        await fn(mcp, "wait_for_app")("app-1", timeout_seconds=601)


async def test_install_plan_and_apply_exact_yaml_once(tools) -> None:
    mcp, sdk = tools
    planned = await fn(mcp, "plan_app_install")(
        "service-deployment",
        "2.4",
        {"image": "repo/image:1", "preset": "cpu-small"},
        app_name="web",
    )
    assert Path(planned["inputs_path"]).exists()
    assert any("discovered schema fields" in item for item in planned["validation"])
    result = await fn(mcp, "install_app")(planned["plan_id"], True)
    assert result["plan_status"] == "applied"
    exact = app_plans.load_yaml_exact(Path(planned["inputs_path"]))
    sdk.apps.install.assert_awaited_once_with(
        app_data=exact, cluster_name="c", org_name="o", project_name="p"
    )
    ledger = Path(os.environ["APOLO_MCP_LEDGER_PATH"]).read_text()
    assert '"resource_id":"app-1"' in ledger
    assert '"state":"created"' in ledger
    with pytest.raises(ValueError, match="consumed"):
        await fn(mcp, "install_app")(planned["plan_id"], True)


async def test_configure_rejects_edited_file_and_revision_drift(tools) -> None:
    mcp, sdk = tools
    planned = await fn(mcp, "plan_app_configure")("app-1", {"preset": "cpu-medium"})
    assert sdk.apps.get_input.await_count == 1
    Path(planned["inputs_path"]).write_text("input: {}\n")
    with pytest.raises(ValueError, match="edited"):
        await fn(mcp, "configure_app")(planned["plan_id"], True)
    sdk.apps.configure.assert_not_awaited()

    planned = await fn(mcp, "plan_app_configure")("app-1", {"preset": "cpu-large"})
    sdk.apps.get_revisions.return_value = [revision(4)]
    with pytest.raises(RuntimeError, match="revision changed"):
        await fn(mcp, "configure_app")(planned["plan_id"], True)
    sdk.apps.configure.assert_not_awaited()
    with pytest.raises(ValueError, match="consumed"):
        await fn(mcp, "configure_app")(planned["plan_id"], True)


async def test_configure_success_preserves_exact_seeded_payload(tools) -> None:
    mcp, sdk = tools
    planned = await fn(mcp, "plan_app_configure")(
        "app-1", {"replicas": 2}, comment="scale"
    )
    result = await fn(mcp, "configure_app")(planned["plan_id"], True)
    assert result["plan_status"] == "applied"
    exact = app_plans.load_yaml_exact(Path(planned["inputs_path"]))
    sdk.apps.configure.assert_awaited_once_with(
        app_id="app-1", app_data=exact, comment="scale"
    )


async def test_rollback_and_uninstall_plan_apply_require_policy(
    tools, monkeypatch
) -> None:
    mcp, sdk = tools
    rollback = await fn(mcp, "plan_app_rollback")("app-1", 3)
    uninstall = await fn(mcp, "plan_app_uninstall")("app-1")
    install = await fn(mcp, "plan_app_install")(
        "service-deployment",
        "2.4",
        {"image": "repo/image:1", "preset": "cpu-small"},
    )
    configure = await fn(mcp, "plan_app_configure")("app-1", {"replicas": 2})
    assert rollback["inputs_path"] is None
    assert uninstall["inputs_path"] is None

    monkeypatch.delenv("APOLO_MCP_ENABLE_HIGH_RISK")
    for operation, planned in (
        ("install_app", install),
        ("configure_app", configure),
        ("rollback_app", rollback),
        ("uninstall_app", uninstall),
    ):
        with pytest.raises(PermissionError, match="server policy"):
            await fn(mcp, operation)(planned["plan_id"], True)
    monkeypatch.setenv("APOLO_MCP_ENABLE_HIGH_RISK", "true")
    await fn(mcp, "rollback_app")(rollback["plan_id"], True)
    sdk.apps.rollback.assert_awaited_once()
    await fn(mcp, "uninstall_app")(uninstall["plan_id"], True)
    sdk.apps.uninstall.assert_awaited_once_with(
        app_id="app-1",
        cluster_name="c",
        org_name="o",
        project_name="p",
        force=False,
    )


async def test_all_apply_tools_require_explicit_approval(tools) -> None:
    mcp, _ = tools
    for name in ("install_app", "configure_app", "rollback_app", "uninstall_app"):
        with pytest.raises(PermissionError, match="approved=true"):
            await fn(mcp, name)("not-used", False)


async def test_truthful_truncation_fetches_one_extra(tools) -> None:
    mcp, sdk = tools
    exact = await fn(mcp, "list_app_templates")(limit=1)
    assert exact["truncated"] is False

    sdk.apps.list_templates = lambda **kwargs: iterator([template(), template()])
    extra = await fn(mcp, "list_app_templates")(limit=1)
    assert len(extra["items"]) == 1
    assert extra["truncated"] is True


async def test_logs_are_memory_bounded_context_checked_and_redacted(tools) -> None:
    mcp, sdk = tools
    sdk.apps.logs = lambda **kwargs: iterator(
        [b"token=visible password:also-visible\n" + b"x" * 1000]
    )
    result = await fn(mcp, "get_app_logs")("app-1", max_bytes=100, max_lines=10)
    assert sdk.apps.get.await_count >= 1
    assert result["truncated"] is True
    assert "visible" not in result["text"]
    assert "<redacted>" in result["text"]
    assert result["bytes"] <= 100


async def test_failed_sdk_apply_is_redacted_and_permanently_consumed(tools) -> None:
    mcp, sdk = tools
    planned = await fn(mcp, "plan_app_install")(
        "service-deployment",
        "2.4",
        {"image": "repo/image:1", "preset": "cpu-small"},
    )
    sdk.apps.install.side_effect = apolo_sdk.ServerNotAvailable(
        "token=credential-value"
    )
    with pytest.raises(RuntimeError) as error:
        await fn(mcp, "install_app")(planned["plan_id"], True)
    assert "credential-value" not in str(error.value)
    _, audit = app_plans.find_plan(planned["plan_id"])
    assert audit["status"] == "failed"
    assert "credential-value" not in audit["failure"]
    with pytest.raises(ValueError, match="consumed"):
        await fn(mcp, "install_app")(planned["plan_id"], True)
    assert sdk.apps.install.await_count == 1
