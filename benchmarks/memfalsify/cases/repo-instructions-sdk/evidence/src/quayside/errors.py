"""Exceptions raised by the SDK.

Errors reported by the API are raised as QuaysideError subclasses.
Network errors from httpx (timeouts, connection failures) propagate unchanged.
"""
from __future__ import annotations

import httpx


class QuaysideError(Exception):
    """Base class for errors the Quayside API reports."""

    @classmethod
    def from_response(cls, response: httpx.Response) -> QuaysideError:
        if response.status_code == 404:
            return NotFound(response.text)
        return APIError(response.status_code, response.text)


class NotFound(QuaysideError):
    """The tracking ID does not exist."""


class APIError(QuaysideError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status


class RateLimited(QuaysideError):
    """Still rate limited after the last attempt; retry_after is the server's hint in seconds."""

    def __init__(self, retry_after: str | None) -> None:
        super().__init__("rate limited")
        self.retry_after = float(retry_after) if retry_after else None
