"""Read-only Model Context Protocol server for MemFalsify and Verified Organizational Memory."""

__version__ = "0.3.0"

from .server import (
    create_server,
)

__all__ = [
    "__version__",
    "create_server",
]
