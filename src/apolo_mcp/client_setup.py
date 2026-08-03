"""Configure supported agent clients for Apolo MCP and packaged skills."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import tomlkit

from .policy import POLICY_MODE_ENV, PolicyMode
from .skill_installer import SKILL_NAMES, install_one, packaged_skills_root


FORWARDED_ENV = (
    "APOLO_CONFIG",
    "APOLO_PASSED_CONFIG",
    POLICY_MODE_ENV,
    "APOLO_MCP_LEDGER_PATH",
    "APOLO_MCP_PLAN_ROOT",
)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _server_args(policy_mode: PolicyMode) -> list[str]:
    return ["serve", "--default-policy", policy_mode.value]


def configure_codex(policy_mode: PolicyMode, *, path: Path | None = None) -> Path:
    """Create or update the user-level Codex MCP registration."""
    config = path or (
        Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    )
    document = (
        tomlkit.parse(config.read_text(encoding="utf-8"))
        if config.exists()
        else tomlkit.document()
    )
    servers = document.get("mcp_servers")
    if servers is None:
        servers = tomlkit.table()
        document["mcp_servers"] = servers
    if not isinstance(servers, dict):
        raise ValueError(f"mcp_servers is not a TOML table in {config}")
    apolo = servers.get("apolo")
    if apolo is None:
        apolo = tomlkit.table()
        servers["apolo"] = apolo
    if not isinstance(apolo, dict):
        raise ValueError(f"mcp_servers.apolo is not a TOML table in {config}")
    apolo["command"] = "apolo-mcp"
    apolo["args"] = _server_args(policy_mode)
    apolo["env_vars"] = list(FORWARDED_ENV)
    _atomic_write(config, tomlkit.dumps(document))
    return config


def _claude_environment() -> dict[str, str]:
    return {
        "APOLO_CONFIG": "${APOLO_CONFIG:-~/.apolo}",
        "APOLO_PASSED_CONFIG": "${APOLO_PASSED_CONFIG:-}",
        POLICY_MODE_ENV: f"${{{POLICY_MODE_ENV}:-}}",
        "APOLO_MCP_LEDGER_PATH": "${APOLO_MCP_LEDGER_PATH:-}",
        "APOLO_MCP_PLAN_ROOT": "${APOLO_MCP_PLAN_ROOT:-}",
    }


def configure_claude(policy_mode: PolicyMode, *, path: Path | None = None) -> Path:
    """Create or update the user-level Claude Code MCP registration."""
    config = path or (Path.home() / ".claude.json")
    if config.exists():
        raw: Any = json.loads(config.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Claude Code configuration is not an object: {config}")
        document = raw
    else:
        document = {}
    servers = document.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"mcpServers is not an object in {config}")
    servers["apolo"] = {
        "type": "stdio",
        "command": "apolo-mcp",
        "args": _server_args(policy_mode),
        "env": _claude_environment(),
    }
    _atomic_write(config, json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    return config


def install_linked_skills(client: str) -> list[tuple[str, Path]]:
    """Link all packaged skills into one supported client's user skill directory."""
    destination_root = (
        Path.home() / (".agents" if client == "codex" else ".claude") / "skills"
    ).resolve()
    source_root = packaged_skills_root()
    results: list[tuple[str, Path]] = []
    for name in SKILL_NAMES:
        source = source_root / name
        destination = destination_root / name
        status = install_one(source, destination, mode="symlink", overwrite=False)
        results.append((status, destination))
    return results


def setup_client(client: str, policy_mode: PolicyMode) -> list[str]:
    """Configure MCP and linked skills for one or both supported clients."""
    clients = ("codex", "claude") if client == "both" else (client,)
    messages: list[str] = []
    for item in clients:
        config = (
            configure_codex(policy_mode)
            if item == "codex"
            else configure_claude(policy_mode)
        )
        messages.append(f"configured {item}: {config}")
        messages.extend(
            f"{status}: {destination}"
            for status, destination in install_linked_skills(item)
        )
    return messages
