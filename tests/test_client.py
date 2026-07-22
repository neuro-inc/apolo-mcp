from contextlib import asynccontextmanager
from types import SimpleNamespace

from apolo_mcp._client import (
    client,
    get_client_provider,
    reset_client_provider,
    set_client_provider,
)


class FakeProvider:
    def __init__(self, value):
        self.value = value

    @asynccontextmanager
    async def client(self):
        yield self.value


async def test_provider_override_is_used_and_reset() -> None:
    original = get_client_provider()
    expected = SimpleNamespace(name="fake")
    token = set_client_provider(FakeProvider(expected))
    try:
        async with client() as actual:
            assert actual is expected
    finally:
        reset_client_provider(token)
    assert get_client_provider() is original
