from pathlib import Path

import pytest

from scripts.install_skills import SKILLS, _destinations, install_one


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
    assert _destinations("project", "both", tmp_path) == [
        (tmp_path / ".codex" / "skills").resolve(),
        (tmp_path / ".claude" / "skills").resolve(),
    ]


def test_shared_requires_explicit_root() -> None:
    with pytest.raises(ValueError, match="--root"):
        _destinations("shared", "codex", None)


def test_default_skill_set_contains_only_runtime_platform_workflows() -> None:
    assert SKILLS == (
        "apolo-platform-context",
        "apolo-research-job",
        "apolo-flow-workloads",
        "apolo-app-rollout",
        "apolo-resource-management",
    )
