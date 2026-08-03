from pathlib import Path

import pytest

from apolo_mcp.catalog import SKILL_SPECS
from apolo_mcp.cli import main as cli_main
from apolo_mcp.skill_installer import (
    SKILL_NAMES,
    destinations,
    install_one,
    packaged_skills_root,
)


def skill(path: Path, content: str = "canonical") -> Path:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(content)
    return path


def test_symlink_install_is_idempotent_and_common(tmp_path: Path) -> None:
    source = skill(tmp_path / "source")
    destination = tmp_path / "home" / "skills" / "example"
    assert (
        install_one(source, destination, mode="symlink", overwrite=False) == "created"
    )
    assert destination.is_symlink()
    assert destination.resolve() == source.resolve()
    assert (
        install_one(source, destination, mode="symlink", overwrite=False) == "unchanged"
    )


def test_copy_refuses_local_modification_without_overwrite(tmp_path: Path) -> None:
    source = skill(tmp_path / "source")
    destination = tmp_path / "skills" / "example"
    install_one(source, destination, mode="copy", overwrite=False)
    assert install_one(source, destination, mode="copy", overwrite=False) == "unchanged"
    (destination / "SKILL.md").write_text("local edit")
    with pytest.raises(FileExistsError, match="--overwrite"):
        install_one(source, destination, mode="copy", overwrite=False)
    assert install_one(source, destination, mode="copy", overwrite=True) == "replaced"
    assert (destination / "SKILL.md").read_text() == "canonical"


def test_project_destinations_are_client_specific(tmp_path: Path) -> None:
    assert destinations("project", "both", tmp_path) == [
        (tmp_path / ".agents" / "skills").resolve(),
        (tmp_path / ".claude" / "skills").resolve(),
    ]


def test_user_destinations_use_supported_skill_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert destinations("user", "both", None) == [
        (tmp_path / ".agents" / "skills").resolve(),
        (tmp_path / ".claude" / "skills").resolve(),
    ]


def test_default_skill_set_contains_only_runtime_platform_workflows() -> None:
    assert SKILL_NAMES == (
        "apolo-platform-user-context",
        "apolo-research-job",
        "apolo-flow-workloads",
        "apolo-applications",
        "apolo-resource-management",
        "apolo-rnd-session-setup",
        "apolo-rnd-session-operate",
    )
    assert SKILL_NAMES == tuple(item.name for item in SKILL_SPECS)


def test_packaged_skills_root_contains_every_skill() -> None:
    root = packaged_skills_root()
    assert all((root / name / "SKILL.md").is_file() for name in SKILL_NAMES)


def test_cli_installs_selected_skill_for_both_clients(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    name = SKILL_NAMES[0]
    skill(source_root / name)
    project = tmp_path / "project"
    assert (
        cli_main(
            [
                "skills",
                "install",
                "--client",
                "both",
                "--target",
                "project",
                "--root",
                str(project),
                "--source",
                str(source_root),
                name,
            ]
        )
        == 0
    )
    assert (project / ".agents" / "skills" / name / "SKILL.md").is_file()
    assert (project / ".claude" / "skills" / name / "SKILL.md").is_file()
    assert (project / ".agents" / "skills" / name).is_symlink()
    assert (project / ".claude" / "skills" / name).is_symlink()


def test_cli_copy_mode_replaces_existing_snapshot(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    name = SKILL_NAMES[0]
    skill(source_root / name, "new")
    project = tmp_path / "project"
    destination = skill(project / ".agents" / "skills" / name, "old")
    assert (
        cli_main(
            [
                "skills",
                "install",
                "--client",
                "codex",
                "--target",
                "project",
                "--root",
                str(project),
                "--mode",
                "copy",
                "--source",
                str(source_root),
                name,
            ]
        )
        == 0
    )
    assert not destination.is_symlink()
    assert (destination / "SKILL.md").read_text() == "new"
