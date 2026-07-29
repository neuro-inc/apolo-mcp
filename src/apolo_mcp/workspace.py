"""Server-controlled confinement for local file operations."""

from __future__ import annotations

import stat
from pathlib import Path


def allowed_workspace_root() -> Path:
    """Return the MCP startup directory used for all local file operations."""
    candidate = Path.cwd()
    try:
        info = candidate.lstat()
    except OSError as exc:
        raise ValueError("allowed workspace must be an existing directory") from exc
    if stat.S_ISLNK(info.st_mode):
        raise ValueError("allowed workspace must not be a symlink")
    root = candidate.resolve(strict=True)
    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode):
        raise ValueError("allowed workspace must be a real directory")
    if root == Path(root.anchor):
        raise ValueError("allowed workspace must not be a filesystem root")
    return root


def resolve_workspace_path(value: str, *, name: str, directory: bool) -> Path:
    """Resolve one existing real path beneath the server-controlled workspace."""
    root = allowed_workspace_root()
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = root / requested
    try:
        lexical_info = requested.lstat()
        resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{name} must be an existing local path") from exc
    if stat.S_ISLNK(lexical_info.st_mode):
        raise ValueError(f"{name} must not be a symlink")
    info = resolved.lstat()
    valid_kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid_kind:
        kind = "directory" if directory else "regular file"
        raise ValueError(f"{name} must be an existing {kind}")
    ensure_path_beneath(resolved, root=root, name=name)
    return resolved


def resolve_new_workspace_file(value: str, *, name: str, create_parents: bool) -> Path:
    """Resolve one new file destination beneath the controlled workspace."""
    root = allowed_workspace_root()
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = root / requested
    if not requested.name or requested.name in {".", ".."}:
        raise ValueError(f"{name} must name one exact file")

    parent = requested.parent
    ensure_path_beneath(parent.resolve(strict=False), root=root, name=name)
    _reject_symlink_parents(parent, root=root, name=name)
    try:
        if create_parents:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        resolved_parent = parent.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{name} parent must be an existing directory") from exc
    ensure_path_beneath(resolved_parent, root=root, name=name)

    target = resolved_parent / requested.name
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"{name} must not already exist; destination exists")
    return target


def ensure_path_beneath(path: Path, *, root: Path, name: str) -> None:
    """Reject a normalized path outside its trusted root."""
    if path != root and root not in path.parents:
        raise PermissionError(f"{name} must be beneath {root}")


def _reject_symlink_parents(parent: Path, *, root: Path, name: str) -> None:
    probe = parent
    while probe != root:
        if probe.exists() and stat.S_ISLNK(probe.lstat().st_mode):
            raise ValueError(f"{name} parents must not be symlinks")
        if probe.parent == probe:
            break
        probe = probe.parent
