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
    configure_xum,
)
from apolo_mcp.policy import PolicyMode
from apolo_mcp.skill_installer import SKILL_NAMES, packaged_skills_root


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


@pytest.mark.parametrize("policy", list(PolicyMode))
def test_configure_xum_creates_config(tmp_path: Path, policy: PolicyMode) -> None:
    config = tmp_path / ".xum" / "mcp.jsonc"
    assert configure_xum(policy, path=config) == config
    assert json.loads(config.read_text()) == {
        "servers": {"apolo": f"apolo-mcp serve --default-policy {policy.value}"}
    }
    assert config.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("text", ["", "// comment", "/* block comment */"])
def test_configure_xum_accepts_empty_config(tmp_path: Path, text: str) -> None:
    config = tmp_path / "mcp.jsonc"
    config.write_text(text)
    configure_xum(PolicyMode.READ_ONLY, path=config)
    assert config.read_text().startswith(text)
    assert '"apolo": "apolo-mcp serve --default-policy read-only"' in config.read_text()


@pytest.mark.parametrize("existing", [False, True])
def test_configure_xum_preserves_jsonc_and_is_idempotent(
    tmp_path: Path, existing: bool
) -> None:
    config = tmp_path / "mcp.jsonc"
    old = '"apolo": {"command": "old"},' if existing else ""
    before = (
        '// header\n{\n  "servers": {\n'
        '    // keep this server\n    "other": "https://example.com/*path*/",\n'
        f"    {old}\n  }},\n"
        '  /* keep plugins */ "enabledPluginServers": ["other",],\n}\n'
    )
    config.write_text(before)
    configure_xum(PolicyMode.MANAGED, path=config)
    after = config.read_text()
    assert "// header\n" in after
    assert '// keep this server\n    "other": "https://example.com/*path*/",' in after
    assert '/* keep plugins */ "enabledPluginServers": ["other",],' in after
    assert '"apolo": "apolo-mcp serve --default-policy managed"' in after
    assert '"command": "old"' not in after
    configure_xum(PolicyMode.MANAGED, path=config)
    assert config.read_text() == after


def test_configure_xum_adds_servers_without_changing_other_properties(
    tmp_path: Path,
) -> None:
    config = tmp_path / "mcp.jsonc"
    config.write_text('{"enabledPluginServers": ["plugin"], /* comment */}\n')
    configure_xum(PolicyMode.READ_ONLY, path=config)
    assert '"enabledPluginServers": ["plugin"], /* comment */' in config.read_text()
    assert (
        '"servers": {"apolo": "apolo-mcp serve --default-policy read-only"}'
        in config.read_text()
    )


@pytest.mark.parametrize(
    "text", ["[]", '{"servers": []}', '{"servers": null}', '{"servers":', "{/*broken}"]
)
def test_configure_xum_rejects_invalid_config_without_writing(
    tmp_path: Path, text: str
) -> None:
    config = tmp_path / "mcp.jsonc"
    config.write_text(text)
    with pytest.raises(ValueError):
        configure_xum(PolicyMode.READ_ONLY, path=config)
    assert config.read_text() == text


def test_cli_setup_xum_links_skills_without_saving_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("XUM_ROOT", raising=False)
    monkeypatch.delenv("MUX_ROOT", raising=False)
    monkeypatch.setenv("APOLO_PASSED_CONFIG", "test-sensitive-value")
    assert cli_main(["setup", "xum", "--policy-mode", "managed"]) == 0
    config = tmp_path / ".xum" / "mcp.jsonc"
    assert "test-sensitive-value" not in config.read_text()
    assert json.loads(config.read_text())["servers"]["apolo"] == (
        "apolo-mcp serve --default-policy managed"
    )
    for name in SKILL_NAMES:
        link = tmp_path / ".xum" / "skills" / name
        assert link.is_symlink()
        assert link.resolve() == packaged_skills_root() / name
    assert cli_main(["setup", "xum", "--policy-mode", "managed"]) == 0


def test_cli_setup_xum_respects_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XUM_ROOT", str(tmp_path / "custom"))
    assert cli_main(["setup", "xum"]) == 0
    assert (tmp_path / "custom" / "mcp.jsonc").is_file()
    assert (tmp_path / "custom" / "skills" / SKILL_NAMES[0]).is_symlink()


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
