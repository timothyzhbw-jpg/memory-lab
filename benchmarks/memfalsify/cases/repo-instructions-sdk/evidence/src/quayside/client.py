"""HTTP client for the Quayside shipment-tracking API."""
from __future__ import annotations

import os
import random
import time
from typing import Any

import httpx

from .errors import QuaysideError, RateLimited
from .models import Shipment

BASE_URL = "https://api.quayside.example/v2"
SANDBOX_URL = "https://sandbox.quayside.example/v2"
DEFAULT_TIMEOUT = 10.0  # seconds, per request
MAX_ATTEMPTS = 3  # total attempts, including the first request
BACKOFF_BASE = 0.5  # seconds, doubled after every failed attempt
# 500 is not retried: the API returns it for request bugs that a retry cannot fix.
RETRY_STATUSES = frozenset({429, 502, 503, 504})


class QuaysideClient:
    """Synchronous client. Reads the API key from QUAYSIDE_API_KEY unless one is passed in."""

    def __init__(self, api_key: str | None = None, *, sandbox: bool = False,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        key = api_key or os.environ.get("QUAYSIDE_API_KEY")
        if not key:
            raise QuaysideError("no API key: pass api_key or set QUAYSIDE_API_KEY")
        self._http = httpx.Client(
            base_url=SANDBOX_URL if sandbox else BASE_URL,
            timeout=timeout,
            headers={"Authorization": f"Bearer {key}"},
            follow_redirects=False,
        )

    def shipment(self, tracking_id: str) -> Shipment:
        return Shipment.model_validate(self._request("GET", f"/shipments/{tracking_id.upper()}"))

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = self._http.request(method, path, **kwargs)
            if response.status_code not in RETRY_STATUSES:
                break
            if attempt == MAX_ATTEMPTS:
                if response.status_code == 429:
                    raise RateLimited(response.headers.get("Retry-After"))
                break
            if "Retry-After" in response.headers:
                # The Quayside API sends Retry-After in seconds, never as an HTTP date.
                delay = float(response.headers["Retry-After"])
            else:
                delay = BACKOFF_BASE * 2 ** (attempt - 1) + random.uniform(0, 0.1)
            time.sleep(delay)
        if response.is_error:
            raise QuaysideError.from_response(response)
        return response.json()
