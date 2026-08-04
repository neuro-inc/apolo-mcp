from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import tomlkit

from apolo_mcp import __version__
from apolo_mcp.cli import main as cli_main
from apolo_mcp.client_setup import (
    FORWARDED_ENV,
    configure_claude,
    configure_codex,
)
from apolo_mcp.policy import PolicyMode


def test_cli_reports_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="0"):
        cli_main(["--version"])

    assert capsys.readouterr().out == f"apolo-mcp {__version__}\n"


def test_configure_codex_preserves_unrelated_settings(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('model = "example"\n')
    assert configure_codex(PolicyMode.MANAGED, path=config) == config
    document = tomlkit.parse(config.read_text())
    assert document["model"] == "example"
    apolo = document["mcp_servers"]["apolo"]
    assert apolo["command"] == "apolo-mcp"
    assert list(apolo["args"]) == ["serve", "--default-policy", "managed"]
    assert list(apolo["env_vars"]) == list(FORWARDED_ENV)


def test_configure_claude_preserves_unrelated_settings(tmp_path: Path) -> None:
    config = tmp_path / ".claude.json"
    config.write_text('{"theme": "dark"}\n')
    assert configure_claude(PolicyMode.FULL, path=config) == config
    document = json.loads(config.read_text())
    assert document["theme"] == "dark"
    apolo = document["mcpServers"]["apolo"]
    assert apolo["args"] == ["serve", "--default-policy", "full"]
    assert set(apolo["env"]) == set(FORWARDED_ENV)
    assert apolo["env"]["APOLO_PASSED_CONFIG"] == "${APOLO_PASSED_CONFIG:-}"


def test_serve_uses_fallback_and_removes_empty_expansions(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    called = False

    def fake_serve() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("apolo_mcp.cli.serve", fake_serve)
    monkeypatch.delenv("APOLO_MCP_POLICY_MODE", raising=False)
    monkeypatch.setenv("APOLO_PASSED_CONFIG", "")
    assert cli_main(["serve", "--default-policy", "managed"]) == 0
    assert called
    assert "APOLO_PASSED_CONFIG" not in os.environ
    assert os.environ["APOLO_MCP_POLICY_MODE"] == "managed"


def test_serve_forwarded_policy_overrides_fallback(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("apolo_mcp.cli.serve", lambda: None)
    monkeypatch.setenv("APOLO_MCP_POLICY_MODE", "full")
    assert cli_main(["serve", "--default-policy", "read-only"]) == 0
    assert os.environ["APOLO_MCP_POLICY_MODE"] == "full"
