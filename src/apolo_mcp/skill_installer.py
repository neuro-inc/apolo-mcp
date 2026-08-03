"""Install the packaged Apolo workflow skills for supported agent clients."""

from __future__ import annotations

import argparse
import filecmp
import shutil
from importlib import resources
from pathlib import Path

from .catalog import SKILL_SPECS


SKILL_NAMES = tuple(item.name for item in SKILL_SPECS)


def _same_tree(left: Path, right: Path) -> bool:
    comparison = filecmp.dircmp(left, right)
    if comparison.left_only or comparison.right_only or comparison.funny_files:
        return False
    if any(
        not filecmp.cmp(left / name, right / name, shallow=False)
        for name in comparison.common_files
    ):
        return False
    return all(_same_tree(left / name, right / name) for name in comparison.common_dirs)


def _client_base(client: str, target: str, root: Path | None) -> Path:
    if target == "user":
        if client == "codex":
            return Path.home() / ".agents"
        return Path.home() / ".claude"
    project = root if root is not None else Path.cwd()
    return project / (".agents" if client == "codex" else ".claude")


def destinations(target: str, client: str, root: Path | None) -> list[Path]:
    """Resolve skill roots for one or both supported clients."""
    clients = ("codex", "claude") if client == "both" else (client,)
    return [
        (_client_base(item, target, root) / "skills").expanduser().resolve()
        for item in clients
    ]


def install_one(source: Path, destination: Path, *, mode: str, overwrite: bool) -> str:
    """Install one skill, returning created, unchanged, or replaced."""
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.is_symlink():
        if destination.resolve() == source.resolve() and mode == "symlink":
            return "unchanged"
        if not overwrite:
            raise FileExistsError(f"refusing to replace existing skill: {destination}")
        destination.unlink()
    elif destination.exists():
        if (
            mode == "symlink"
            and destination.is_dir()
            and _same_tree(source, destination)
        ):
            shutil.rmtree(destination)
            destination.symlink_to(source.resolve(), target_is_directory=True)
            return "replaced"
        if mode == "copy" and destination.is_dir() and _same_tree(source, destination):
            return "unchanged"
        if not overwrite:
            raise FileExistsError(
                f"local skill differs; rerun with --overwrite: {destination}"
            )
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    if mode == "symlink":
        destination.symlink_to(source.resolve(), target_is_directory=True)
    else:
        shutil.copytree(source, destination)
    return "replaced" if overwrite else "created"


def packaged_skills_root() -> Path:
    """Return skills from a checkout or from the installed wheel."""
    checkout = Path(__file__).resolve().parents[2] / "skills"
    if checkout.is_dir():
        return checkout
    packaged = resources.files("apolo_mcp").joinpath("skills")
    root = Path(str(packaged))
    if not root.is_dir():
        raise FileNotFoundError("the installed apolo-mcp package contains no skills")
    return root


def add_install_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--client", choices=("codex", "claude", "both"), required=True)
    parser.add_argument("--target", choices=("user", "project"), default="user")
    parser.add_argument(
        "--root",
        type=Path,
        help="project root for target project (default: current directory)",
    )
    parser.add_argument("--mode", choices=("copy", "symlink"), default="symlink")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--source", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("names", nargs="*", metavar="SKILL")


def install_from_args(args: argparse.Namespace) -> int:
    names = tuple(args.names) or SKILL_NAMES
    unknown = sorted(set(names) - set(SKILL_NAMES))
    if unknown:
        raise ValueError(f"unknown skill name(s): {', '.join(unknown)}")
    source_root = (
        args.source.expanduser().resolve()
        if args.source is not None
        else packaged_skills_root()
    )
    for destination_root in destinations(args.target, args.client, args.root):
        for name in names:
            source = source_root / name
            if not (source / "SKILL.md").is_file():
                raise FileNotFoundError(f"canonical skill is missing: {source}")
            status = install_one(
                source,
                destination_root / name,
                mode=args.mode,
                overwrite=args.overwrite or args.mode == "copy",
            )
            print(f"{status}: {destination_root / name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_install_arguments(parser)
    args = parser.parse_args(argv)
    try:
        return install_from_args(args)
    except (FileExistsError, FileNotFoundError, ValueError) as error:
        parser.error(str(error))
