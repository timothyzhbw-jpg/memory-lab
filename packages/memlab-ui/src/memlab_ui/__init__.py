"""Local, read-only web page for MemFalsify and Verified Organizational Memory."""

__version__ = "0.1.0"

from .server import (
    create_server,
)

__all__ = [
    "__version__",
    "create_server",
]
