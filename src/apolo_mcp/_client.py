"""SDK client providers keep tool logic independent from transport and identity."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import AsyncContextManager, Protocol, runtime_checkable

import apolo_sdk


@runtime_checkable
class ApoloClientProvider(Protocol):
    """Create an isolated SDK client for one tool invocation."""

    def client(self) -> AsyncContextManager[apolo_sdk.Client]: ...


class LocalApoloClientProvider:
    """Load the current user's local Apolo configuration for stdio operation."""

    def client(self) -> AsyncContextManager[apolo_sdk.Client]:
        return apolo_sdk.get()


_provider: ContextVar[ApoloClientProvider] = ContextVar(
    "apolo_client_provider", default=LocalApoloClientProvider()
)


def get_client_provider() -> ApoloClientProvider:
    return _provider.get()


def set_client_provider(provider: ApoloClientProvider):  # type: ignore[no-untyped-def]
    """Override the provider in the current context; intended for tests/embedding."""
    return _provider.set(provider)


def reset_client_provider(token) -> None:  # type: ignore[no-untyped-def]
    _provider.reset(token)


@asynccontextmanager
async def client() -> AsyncIterator[apolo_sdk.Client]:
    """Compatibility helper used by tool modules."""
    async with get_client_provider().client() as sdk_client:
        yield sdk_client
