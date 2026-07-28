#!/usr/bin/env python3
"""Install the canonical Apolo skills into Codex and/or Claude homes."""

from __future__ import annotations

import argparse
import filecmp
import os
import shutil
from pathlib import Path


SKILLS = (
    "apolo-platform-user-context",
    "apolo-research-job",
    "apolo-flow-workloads",
    "apolo-applications",
    "apolo-resource-management",
    "apolo-rnd-session-setup",
    "apolo-rnd-session-operate",
)


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


def _destinations(target: str, client: str, root: Path | None) -> list[Path]:
    clients = ("codex", "claude") if client == "both" else (client,)
    result: list[Path] = []
    for item in clients:
        if target == "user":
            env_name = "CODEX_HOME" if item == "codex" else "CLAUDE_HOME"
            default = Path.home() / (".codex" if item == "codex" else ".claude")
            base = Path(os.environ.get(env_name, default))
        else:
            if root is None:
                raise ValueError(f"--root is required for target {target}")
            if target == "project":
                base = root / (".codex" if item == "codex" else ".claude")
            else:
                base = root / item
        result.append(base.expanduser().resolve() / "skills")
    return result


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", choices=("user", "project", "shared"), required=True
    )
    parser.add_argument("--client", choices=("codex", "claude", "both"), default="both")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--mode", choices=("copy", "symlink"), default="symlink")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--source", type=Path, default=Path(__file__).parents[1] / "skills"
    )
    parser.add_argument("names", nargs="*")
    args = parser.parse_args()
    unknown = sorted(set(args.names) - set(SKILLS))
    if unknown:
        parser.error(f"unknown skill name(s): {', '.join(unknown)}")
    return args


def main() -> int:
    args = parse_args()
    names = args.names or list(SKILLS)
    source_root = args.source.expanduser().resolve()
    for destination_root in _destinations(args.target, args.client, args.root):
        for name in names:
            source = source_root / name
            if not (source / "SKILL.md").is_file():
                raise FileNotFoundError(f"canonical skill is missing: {source}")
            status = install_one(
                source,
                destination_root / name,
                mode=args.mode,
                overwrite=args.overwrite,
            )
            print(f"{status}: {destination_root / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
