import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest

from apolo_mcp import app_plans


@pytest.fixture(autouse=True)
def plan_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "allowed"
    monkeypatch.setenv(app_plans.PLAN_ROOT_ENV, str(root))
    return root


def test_create_plan_writes_review_artifacts_atomically(plan_root: Path) -> None:
    plan = app_plans.create_plan(
        kind="install",
        target="service/deployment",
        context={"cluster": "c", "org": "o", "project": "p"},
        payload={
            "template_name": "service-deployment",
            "template_version": "1",
            "input": {"secret": "secret:database"},
        },
        ttl_seconds=60,
        details={
            "validation": ["ok"],
            "destructive_effects": ["creates resources"],
        },
    )

    directory = Path(plan["inputs_path"]).parent
    assert plan_root in directory.parents
    assert {item.name for item in directory.iterdir()} == {
        "inputs.yaml",
        "plan.json",
        "PLAN.md",
    }
    stored = json.loads((directory / "plan.json").read_text())
    assert stored["inputs_sha256"] == plan["inputs_sha256"]
    assert "service-deployment" in (directory / "inputs.yaml").read_text()
    assert not list(directory.glob(".*"))


@pytest.mark.parametrize(
    "payload",
    [
        {"input": {"password": "plain-text"}},
        {"input": {"private_key": "-----BEGIN RSA PRIVATE KEY-----"}},
        {"input": {"token": "a" * 50}},
    ],
)
def test_inline_secrets_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="secret value|credential"):
        app_plans.create_plan(
            kind="install",
            target="app",
            context={"cluster": "c", "org": "o", "project": "p"},
            payload=payload,
            ttl_seconds=60,
            details={},
        )


def test_edited_file_and_context_are_rejected() -> None:
    context = {"cluster": "c", "org": "o", "project": "p"}
    plan = app_plans.create_plan(
        kind="configure",
        target="app-1",
        context=context,
        payload={"input": {"replicas": 1}},
        ttl_seconds=60,
        details={},
    )
    Path(plan["inputs_path"]).write_text("input:\n  replicas: 2\n")
    with pytest.raises(ValueError, match="edited"):
        app_plans.validate_for_apply(plan["id"], kind="configure", context=context)

    Path(plan["inputs_path"]).write_text("input:\n  replicas: 1\n")
    with pytest.raises(ValueError, match="context"):
        app_plans.validate_for_apply(
            plan["id"],
            kind="configure",
            context={**context, "project": "other"},
        )


def test_expired_and_consumed_plans_are_rejected() -> None:
    context = {"cluster": "c", "org": "o", "project": "p"}
    plan = app_plans.create_plan(
        kind="uninstall",
        target="app",
        context=context,
        payload=None,
        ttl_seconds=60,
        details={},
    )
    path, stored, _ = app_plans.validate_for_apply(
        plan["id"], kind="uninstall", context=context
    )
    app_plans.record_success(path, stored, {"status": "uninstalling"})
    with pytest.raises(ValueError, match="consumed"):
        app_plans.validate_for_apply(plan["id"], kind="uninstall", context=context)

    other = app_plans.create_plan(
        kind="rollback",
        target="app",
        context=context,
        payload=None,
        ttl_seconds=60,
        details={},
    )
    other_path, document = app_plans.find_plan(other["id"])
    document["expires_at"] = (app_plans.utc_now() - timedelta(seconds=1)).isoformat()
    other_path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="expired"):
        app_plans.validate_for_apply(other["id"], kind="rollback", context=context)


def test_atomic_claim_allows_only_one_executor() -> None:
    context = {"cluster": "c", "org": "o", "project": "p"}
    plan = app_plans.create_plan(
        kind="install",
        target="app",
        context=context,
        payload={"input": {}},
        ttl_seconds=60,
        details={},
    )

    def claim() -> bool:
        try:
            app_plans.claim_for_apply(plan["id"], kind="install", context=context)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: claim(), range(2)))
    assert sorted(results) == [False, True]
    _, audit = app_plans.find_plan(plan["id"])
    assert audit["status"] == "applying"


def test_protected_root_and_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protected = tmp_path / "not-a-directory"
    protected.write_text("protected")
    monkeypatch.setenv(app_plans.PLAN_ROOT_ENV, str(protected))
    with pytest.raises((FileExistsError, NotADirectoryError)):
        app_plans.plan_root()

    monkeypatch.setenv(app_plans.PLAN_ROOT_ENV, str(tmp_path / "ok"))
    with pytest.raises(ValueError, match="target"):
        app_plans.create_plan(
            kind="install",
            target="../../",
            context={"cluster": "c", "org": "o", "project": "p"},
            payload={},
            ttl_seconds=60,
            details={},
        )
