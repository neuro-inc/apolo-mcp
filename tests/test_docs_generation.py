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
    reference = (ROOT / "docs" / "capabilities" / "tools.md").read_text()
    skills = (ROOT / "docs" / "capabilities" / "skills.md").read_text()
    assert all(reference.count(f"### `{tool.name}`") == 1 for _, tool, _ in tools)
    assert all(skills.count(f"**Skill name:** `{name}`") == 1 for name in module.SKILLS)


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
    sources = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
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
        ROOT / "docs" / "getting-started" / "safety.md",
        ROOT / "docs" / "capabilities" / "tools.md",
        ROOT / "docs" / "capabilities" / "skills.md",
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


def test_gitbook_navigation_excludes_generator_sources() -> None:
    summary = (ROOT / "docs" / "SUMMARY.md").read_text(encoding="utf-8")
    assert "_templates" not in summary
    assert not (ROOT / "docs" / "_templates").exists()
    assert (ROOT / "build-tools" / "docs-templates" / "safety.md").is_file()
    assert summary.index("[Getting started]") < summary.index("[Capabilities]")
    assert summary.index("[Capabilities]") < summary.index("[Guides]")
    for target in (
        "getting-started/safety.md",
        "capabilities/tools.md",
        "capabilities/skills.md",
    ):
        assert target in summary
