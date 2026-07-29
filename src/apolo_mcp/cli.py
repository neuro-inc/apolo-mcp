"""Command-line interface for the Apolo MCP server and packaged skills."""

from __future__ import annotations

import argparse
import sys

from .server import main as serve
from .skill_installer import add_install_arguments, install_from_args


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apolo-mcp", description=__doc__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="run the stdio MCP server")
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
        serve()
        return 0
    if args.command == "skills" and args.skills_command == "install":
        try:
            return install_from_args(args)
        except (FileExistsError, FileNotFoundError, ValueError) as error:
            parser.error(str(error))
    parser.error("a command is required")
