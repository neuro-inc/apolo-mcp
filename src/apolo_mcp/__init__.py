"""A safe, typed MCP adapter for the Apolo platform."""

from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("apolo-mcp")
except PackageNotFoundError:  # pragma: no cover - source tree without installation
    __version__ = "0.0.0+unknown"

__all__ = ("__version__",)
