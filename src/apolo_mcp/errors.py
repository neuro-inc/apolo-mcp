"""Credential-safe, actionable errors for model-facing tool results."""

import re
from dataclasses import dataclass

import apolo_sdk


_SENSITIVE = re.compile(
    r"(?i)(authorization|cookie|token|password|secret|api[-_]?key)"
    r"(\s*[:=]\s*|\s+)([^\s,;]+)"
)
_URL_CREDENTIALS = re.compile(r"(://)[^/@\s]+@")


def sanitize_message(value: object, *, limit: int = 500) -> str:
    message = " ".join(str(value).split())
    message = _URL_CREDENTIALS.sub(r"\1<redacted>@", message)
    message = _SENSITIVE.sub(r"\1\2<redacted>", message)
    return message[:limit] or "No details were provided"


@dataclass(frozen=True)
class ApoloToolError(RuntimeError):
    operation: str
    message: str
    retryable: bool
    remediation: str
    context: dict[str, str] | None = None
    resource: str | None = None

    def __str__(self) -> str:
        target = "/".join(self.context.values()) if self.context else "unresolved"
        resource = (
            f"; resource={sanitize_message(self.resource)}" if self.resource else ""
        )
        return (
            f"{self.operation} failed in context={target}{resource}; "
            f"retryable={str(self.retryable).lower()}: {self.message}. "
            f"Remediation: {self.remediation}"
        )


def normalize_error(
    exc: Exception,
    *,
    operation: str,
    context: dict[str, str] | None = None,
    resource: str | None = None,
) -> ApoloToolError:
    auth_types = (apolo_sdk.AuthenticationError, apolo_sdk.AuthorizationError)
    retry_types = (apolo_sdk.ServerNotAvailable, apolo_sdk.BadGateway, TimeoutError)
    not_found_types = (apolo_sdk.ResourceNotFound,)
    if isinstance(exc, auth_types):
        remediation = "Run `apolo login` or verify access to the resolved context"
    elif isinstance(exc, not_found_types):
        remediation = "Verify the resource identifier and resolved context"
    elif isinstance(exc, retry_types):
        remediation = (
            "Retry with bounded backoff; contact the platform admin if persistent"
        )
    else:
        remediation = "Check the arguments and resolved Apolo context"
    return ApoloToolError(
        operation=operation,
        message=sanitize_message(exc),
        retryable=isinstance(exc, retry_types),
        remediation=remediation,
        context=context,
        resource=sanitize_message(resource) if resource else None,
    )
