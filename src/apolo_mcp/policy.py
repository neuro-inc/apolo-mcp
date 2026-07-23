"""Local user-controlled high-risk operation policy loaded from environment or JSON."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


POLICY_FILE_ENV = "APOLO_MCP_POLICY_FILE"
HIGH_RISK_ENV = "APOLO_MCP_ENABLE_HIGH_RISK"


@dataclass(frozen=True)
class Policy:
    # This is a deliberate opt-in by the user who launches the local stdio server.
    # It is not an administrator control and cannot elevate the user's Apolo RBAC.
    enable_high_risk: bool = False

    @classmethod
    def load(cls) -> "Policy":
        path = os.environ.get(POLICY_FILE_ENV)
        file_value: bool | None = None
        if path:
            raw: Any = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(
                raw.get("enable_high_risk", False), bool
            ):
                raise ValueError("Policy must contain a boolean 'enable_high_risk'")
            file_value = raw.get("enable_high_risk", False)
        env = os.environ.get(HIGH_RISK_ENV)
        if env is not None:
            normalized = env.strip().lower()
            if normalized not in {"0", "1", "false", "true", "no", "yes"}:
                raise ValueError(f"{HIGH_RISK_ENV} must be a boolean")
            return cls(enable_high_risk=normalized in {"1", "true", "yes"})
        return cls(enable_high_risk=file_value or False)

    def require_high_risk(self, operation: str) -> None:
        if not self.enable_high_risk:
            raise PermissionError(
                f"Operation {operation!r} is disabled by server policy; the user "
                f"running the server must explicitly set {HIGH_RISK_ENV}=true"
            )
