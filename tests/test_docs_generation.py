from __future__ import annotations

import asyncio
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import apolo_sdk


ROOT = Path(__file__).parents[1]
LOCAL_LINK = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")


def _generator() -> ModuleType:
    path = ROOT / "build-tools" / "generate-docs.py"
    spec = importlib.util.spec_from_file_location("generate_docs", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_documentation_is_current() -> None:
    result = subprocess.run(
        [sys.executable, "build-tools/generate-docs.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_tool_collection_uses_metadata_without_sdk_client(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    def forbidden(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("documentation generation created an SDK client")

    monkeypatch.setattr(apolo_sdk, "get", forbidden)
    module = _generator()
    tools = asyncio.run(module.collect_tools())
    names = [tool.name for _, tool, _ in tools]
    assert len(names) == len(set(names))
    assert {kind for _, _, kind in tools} == {
        "read-only",
        "planning",
        "write",
        "destructive",
    }


def test_generated_reference_covers_every_tool_and_skill() -> None:
    module = _generator()
    tools = asyncio.run(module.collect_tools())
    skills = (ROOT / "docs" / "capabilities" / "skills.md").read_text()
    references = {
        path.stem: path.read_text(encoding="utf-8")
        for path in (ROOT / "docs" / "capabilities" / "tools").glob("*.md")
        if path.name != "README.md"
    }
    expected_groups = {item.slug for item in module.CAPABILITY_SPECS}
    assert references.keys() == expected_groups
    for group, tool, _ in tools:
        page = references[module._group_slug(group)]
        assert page.count(f"## `{tool.name}`") == 1
        assert all(
            f"## `{tool.name}`" not in other_page
            for slug, other_page in references.items()
            if slug != module._group_slug(group)
        )
    assert all(
        skills.count(f"**Skill name:** `{item.name}`") == 1
        for item in module.SKILL_SPECS
    )
    detail_root = ROOT / "docs" / "capabilities" / "skills"
    details = {
        path.parent.name: path.read_text(encoding="utf-8")
        for path in detail_root.glob("*/README.md")
    }
    assert details.keys() == {item.name for item in module.SKILL_SPECS}
    for item in module.SKILL_SPECS:
        source_root = ROOT / "skills" / item.name
        assert (
            module._skill_instructions(source_root / "SKILL.md") in details[item.name]
        )
        expected_references = (
            {path.name for path in (source_root / "references").glob("*.md")}
            if (source_root / "references").is_dir()
            else set()
        )
        generated_references = {
            path.name for path in (detail_root / item.name / "references").glob("*.md")
        }
        assert generated_references == expected_references


def test_tool_index_describes_groups_without_counts() -> None:
    module = _generator()
    index = (ROOT / "docs" / "capabilities" / "tools" / "README.md").read_text(
        encoding="utf-8"
    )
    for capability in module.CAPABILITY_SPECS:
        assert capability.description in index
    assert not re.search(r"\b\d+ tools\b", index)


def test_check_mode_reports_stale_document(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    module = _generator()
    template = tmp_path / "template.md"
    output = tmp_path / "output.md"
    template.write_text("# {title}\n")
    output.write_text("stale\n")
    document = module.Document(template, output, {"title": "Current"})
    assert module._update(document, check=True) is False
    diff = capsys.readouterr().out
    assert "-stale" in diff
    assert "+# Current" in diff
    assert output.read_text() == "stale\n"


def test_local_documentation_links_resolve() -> None:
    sources = [
        ROOT / "README.md",
        *sorted((ROOT / "docs").rglob("*.md")),
        *sorted((ROOT / "skills").rglob("*.md")),
    ]
    broken: list[str] = []
    for source in sources:
        for target in LOCAL_LINK.findall(source.read_text(encoding="utf-8")):
            path = target.split("#", 1)[0]
            if not path or "://" in path or path.startswith("mailto:"):
                continue
            if not (source.parent / path).resolve().exists():
                broken.append(f"{source.relative_to(ROOT)} -> {target}")
    assert not broken, "broken local documentation links:\n" + "\n".join(broken)


def test_documentation_has_no_roadmap_material() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "docs").rglob("*.md"))
    ).lower()
    assert "roadmap" not in text
    assert "future provider" not in text
    assert "future plan" not in text


def test_capability_matrix_uses_complete_public_cli_commands() -> None:
    matrix = (ROOT / "docs" / "capabilities" / "README.md").read_text(encoding="utf-8")
    assert "`apolo config show`" in matrix
    assert "`apolo job run`, `apolo run`" in matrix
    assert "`apolo-flow ps`" in matrix
    assert "job/root" not in matrix
    assert "storage/root" not in matrix
    assert "image/root" not in matrix


def test_overview_explains_credential_creation_boundary() -> None:
    overview = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    safety = (ROOT / "docs" / "getting-started" / "safety.md").read_text(
        encoding="utf-8"
    )
    for text in (overview, safety):
        assert "create service accounts" in text
        assert "one-time token" in text
        assert "protected" in text
        assert "model" in text


def test_generated_documentation_has_no_trailing_whitespace() -> None:
    generated = (
        ROOT / "docs" / "getting-started" / "installation.md",
        ROOT / "docs" / "getting-started" / "safety.md",
        ROOT / "docs" / "guides" / "full-mode-service-account.md",
        ROOT / "docs" / "capabilities" / "skills.md",
        ROOT
        / "skills"
        / "apolo-rnd-session-operate"
        / "references"
        / "installation.md",
        *sorted((ROOT / "docs" / "capabilities" / "tools").glob("*.md")),
        *sorted((ROOT / "docs" / "capabilities" / "skills").rglob("*.md")),
    )
    offenders = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in generated
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if line != line.rstrip()
    ]
    assert not offenders, "trailing whitespace in generated docs: " + ", ".join(
        offenders
    )


def test_rnd_runtime_fragment_is_shared_by_advanced_guide_and_skill() -> None:
    installation = (ROOT / "docs" / "getting-started" / "installation.md").read_text()
    guide = (ROOT / "docs" / "guides" / "full-mode-service-account.md").read_text()
    packaged = (
        ROOT / "skills" / "apolo-rnd-session-operate" / "references" / "installation.md"
    ).read_text()
    fragment = (ROOT / "build-tools" / "docs-templates" / "rnd-runtime.md").read_text()
    assert fragment not in installation
    assert fragment in guide
    assert fragment in packaged
    assert "../guides/full-mode-service-account.md" in installation
    assert "../../apolo-rnd-session-setup/SKILL.md" in packaged


def test_installation_forwards_complete_apolo_environment_contract() -> None:
    installation = (ROOT / "docs" / "getting-started" / "installation.md").read_text()
    for variable in (
        "APOLO_CONFIG",
        "APOLO_PASSED_CONFIG",
        "APOLO_MCP_POLICY_MODE",
        "APOLO_MCP_LEDGER_PATH",
        "APOLO_MCP_PLAN_ROOT",
    ):
        assert variable in installation
    assert "APOLO_API_TOKEN" in installation
    assert "must not" in installation


def test_gitbook_navigation_excludes_generator_sources() -> None:
    summary = (ROOT / "docs" / "SUMMARY.md").read_text(encoding="utf-8")
    assert "_templates" not in summary
    assert not (ROOT / "docs" / "_templates").exists()
    assert (ROOT / "build-tools" / "docs-templates" / "safety.md").is_file()
    assert (ROOT / "build-tools" / "docs-templates" / "installation.md").is_file()
    assert (
        ROOT / "build-tools" / "docs-templates" / "full-mode-service-account.md"
    ).is_file()
    assert (ROOT / "build-tools" / "docs-templates" / "rnd-runtime.md").is_file()
    assert summary.index("[Getting started]") < summary.index("[Capabilities]")
    assert summary.index("[Capabilities]") < summary.index("[Guides]")
    for target in (
        "getting-started/installation.md",
        "getting-started/safety.md",
        "capabilities/tools/README.md",
        "capabilities/tools/context.md",
        "capabilities/tools/service-accounts.md",
        "capabilities/skills.md",
        "capabilities/skills/apolo-platform-user-context/README.md",
        "capabilities/skills/apolo-research-job/README.md",
        "capabilities/skills/apolo-flow-workloads/README.md",
        "capabilities/skills/apolo-applications/README.md",
        "capabilities/skills/apolo-resource-management/README.md",
        "capabilities/skills/apolo-rnd-session-setup/README.md",
        "capabilities/skills/apolo-rnd-session-operate/README.md",
    ):
        assert target in summary


def test_safety_is_grouped_by_skill_then_operation_type() -> None:
    module = _generator()
    tools = asyncio.run(module.collect_tools())
    safety = (ROOT / "docs" / "getting-started" / "safety.md").read_text(
        encoding="utf-8"
    )
    positions = [
        safety.index(f"## [{skill.display_name}]") for skill in module.SKILL_SPECS
    ]
    assert positions == sorted(positions)
    for index, skill in enumerate(module.SKILL_SPECS):
        start = safety.index(f"## [{skill.display_name}]")
        end = positions[index + 1] if index + 1 < len(positions) else len(safety)
        section = safety[start:end]
        assert section.index("### Read-only operations") < section.index(
            "### Write operations"
        )
        assert section.index("### Write operations") < section.index(
            "### Destructive operations"
        )
    assert all(safety.count(f"[`{tool.name}`]") == 1 for _, tool, _ in tools)
    assert "Local planning; does not mutate Apolo resources." in safety
