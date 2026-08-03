import apolo_sdk

from apolo_mcp.errors import normalize_error, sanitize_message


def test_sanitize_message_redacts_and_bounds() -> None:
    message = sanitize_message("Authorization: Bearer-value token=abc " + "x" * 600)
    assert "Bearer-value" not in message
    assert "abc" not in message
    assert "<redacted>" in message
    assert len(message) == 500


def test_normalize_auth_error_is_actionable_and_safe() -> None:
    error = normalize_error(
        apolo_sdk.AuthenticationError("cookie=session-value"),
        operation="list_projects",
        context={"cluster": "alpha", "org": "team", "project": "p"},
    )
    rendered = str(error)
    assert "session-value" not in rendered
    assert "apolo login" in rendered
    assert "retryable=false" in rendered


def test_server_error_is_retryable() -> None:
    error = normalize_error(
        apolo_sdk.ServerNotAvailable("temporarily unavailable"),
        operation="list_clusters",
    )
    assert error.retryable is True
