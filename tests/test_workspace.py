from pathlib import Path

import pytest

from apolo_mcp.workspace import (
    allowed_workspace_root,
    resolve_new_workspace_file,
    resolve_workspace_path,
)


def test_server_startup_directory_is_the_default_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source.txt"
    source.write_text("safe")

    assert allowed_workspace_root() == tmp_path
    assert (
        resolve_workspace_path("source.txt", name="source_file", directory=False)
        == source
    )


def test_path_outside_startup_directory_is_rejected(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    monkeypatch.chdir(root)

    with pytest.raises(PermissionError, match="must be beneath"):
        resolve_workspace_path(str(outside), name="source_file", directory=False)


def test_new_destination_is_resolved_and_bounded_centrally(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    monkeypatch.chdir(root)

    assert (
        resolve_new_workspace_file(
            "nested/result.txt", name="destination_file", create_parents=True
        )
        == root / "nested/result.txt"
    )
    with pytest.raises(PermissionError, match="must be beneath"):
        resolve_new_workspace_file(
            "../escaped.txt", name="destination_file", create_parents=True
        )


def test_new_destination_rejects_symlink_parent(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    target = root / "target"
    root.mkdir()
    target.mkdir()
    (root / "link").symlink_to(target, target_is_directory=True)
    monkeypatch.chdir(root)

    with pytest.raises(ValueError, match="parents must not be symlinks"):
        resolve_new_workspace_file(
            "link/result.txt", name="destination_file", create_parents=True
        )


def test_filesystem_root_is_never_an_allowed_workspace(monkeypatch):
    root = Path(Path.cwd().anchor)
    monkeypatch.chdir(root)

    with pytest.raises(ValueError, match="filesystem root"):
        allowed_workspace_root()
