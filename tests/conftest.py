from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_process_policy():
    """Give each test a fresh process-level policy selection."""
    from apolo_mcp.policy import _reset_policy_for_tests

    _reset_policy_for_tests()
    yield
    _reset_policy_for_tests()


@pytest.fixture()
def mock_client():
    """Provide a mock apolo_sdk.Client for unit tests.

    Usage:
        async def test_something(mock_client):
            mock_client.jobs.status.return_value = MagicMock(...)
            ...
    """
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("apolo_mcp._client.apolo_sdk.get", return_value=client):
        yield client
