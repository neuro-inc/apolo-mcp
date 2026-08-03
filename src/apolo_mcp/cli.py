"""Command-line interface for the Apolo MCP server and packaged skills."""

from __future__ import annotations

import argparse
import os
import sys

from .client_setup import setup_client
from .policy import POLICY_MODE_ENV, PolicyMode
from .server import main as serve
from .skill_installer import add_install_arguments, install_from_args


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apolo-mcp", description=__doc__)
    commands = parser.add_subparsers(dest="command")
    server = commands.add_parser("serve", help="run the stdio MCP server")
    server.add_argument(
        "--default-policy",
        choices=tuple(item.value for item in PolicyMode),
        default=PolicyMode.READ_ONLY.value,
        help="policy used when APOLO_MCP_POLICY_MODE is not forwarded",
    )
    setup = commands.add_parser(
        "setup", help="configure MCP and linked skills for an agent client"
    )
    setup.add_argument("client", choices=("codex", "claude", "both"))
    setup.add_argument(
        "--policy-mode",
        choices=tuple(item.value for item in PolicyMode),
        default=PolicyMode.READ_ONLY.value,
        help="default MCP policy (default: read-only)",
    )
    skills = commands.add_parser("skills", help="manage packaged workflow skills")
    skill_commands = skills.add_subparsers(dest="skills_command", required=True)
    install = skill_commands.add_parser(
        "install", help="install skills for Codex and/or Claude Code"
    )
    add_install_arguments(install)
    return parser


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if not values:
        serve()
        return 0
    parser = _parser()
    args = parser.parse_args(values)
    if args.command == "serve":
        for name in (
            "APOLO_PASSED_CONFIG",
            POLICY_MODE_ENV,
            "APOLO_MCP_LEDGER_PATH",
            "APOLO_MCP_PLAN_ROOT",
        ):
            if os.environ.get(name) == "":
                os.environ.pop(name)
        os.environ.setdefault(POLICY_MODE_ENV, args.default_policy)
        serve()
        return 0
    if args.command == "setup":
        try:
            messages = setup_client(args.client, PolicyMode(args.policy_mode))
        except (FileExistsError, FileNotFoundError, ValueError) as error:
            parser.error(str(error))
        for message in messages:
            print(message)
        return 0
    if args.command == "skills" and args.skills_command == "install":
        try:
            return install_from_args(args)
        except (FileExistsError, FileNotFoundError, ValueError) as error:
            parser.error(str(error))
    parser.error("a command is required")
