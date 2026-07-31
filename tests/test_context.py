from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apolo_mcp.context import resolve_context


def make_config():
    return SimpleNamespace(
        username="user@example.test",
        cluster_name="alpha",
        org_name="team",
        project_name="default",
        clusters={
            "alpha": SimpleNamespace(orgs=["team"]),
            "beta": SimpleNamespace(orgs=["other"]),
        },
        projects={
            "one": SimpleNamespace(
                cluster_name="alpha", org_name="team", name="default"
            ),
            "two": SimpleNamespace(
                cluster_name="beta", org_name="other", name="explicit"
            ),
        },
        switch_cluster=Mock(),
        switch_org=Mock(),
        switch_project=Mock(),
    )


def test_resolve_defaults_without_persisting() -> None:
    config = make_config()
    result = resolve_context(config)
    assert result.as_dict() == {
        "username": "user@example.test",
        "cluster": "alpha",
        "org": "team",
        "project": "default",
    }
    config.switch_cluster.assert_not_called()
    config.switch_org.assert_not_called()
    config.switch_project.assert_not_called()


def test_resolve_explicit_context() -> None:
    result = resolve_context(
        make_config(), cluster="beta", org="other", project="explicit"
    )
    assert result.as_dict() == {
        "username": "user@example.test",
        "cluster": "beta",
        "org": "other",
        "project": "explicit",
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"cluster": "missing"}, "Unknown cluster"),
        ({"cluster": "beta", "org": "team"}, "Organization"),
        (
            {"cluster": "alpha", "org": "team", "project": "missing"},
            "Project",
        ),
    ],
)
def test_invalid_context_is_rejected(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_context(make_config(), **kwargs)
