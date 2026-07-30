"""Credential-safety checks shared by structured configuration writers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_SENSITIVE_KEY = re.compile(
    r"(?i)(password|passwd|token|secret(?:_?value)?|api[-_]?key|private[-_]?key)"
)
_SECRET_VALUE = re.compile(r"(?i)^(?:bearer\s+|-----BEGIN .*PRIVATE KEY-----)")


def _is_reference(value: Any) -> bool:
    return isinstance(value, str) and (
        value.startswith(("secret:", "apolo-secret:"))
        or (value.startswith("${{") and value.endswith("}}"))
    )


def ensure_secret_references_only(value: Any, path: str = "input") -> None:
    """Reject likely inline credentials while allowing explicit references."""
    if isinstance(value, Mapping):
        reference_type = str(value.get("type", "")).lower()
        is_reference = reference_type in {
            "secret",
            "secret-ref",
            "secret-reference",
            "app-instance-ref",
        }
        for key, item in value.items():
            item_path = f"{path}.{key}"
            if (
                _SENSITIVE_KEY.search(str(key))
                and not isinstance(item, (Mapping, list))
                and item not in (None, "")
                and not (is_reference or _is_reference(item))
            ):
                raise ValueError(
                    f"{item_path} may contain a secret value; use a secret reference"
                )
            ensure_secret_references_only(item, item_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            ensure_secret_references_only(item, f"{path}[{index}]")
    elif isinstance(value, str) and _SECRET_VALUE.search(value):
        if not _is_reference(value):
            raise ValueError(f"{path} looks like inline credential material")
