# Changelog

## Unreleased

- `QuaysideClient.shipment()` accepts lowercase tracking IDs.

## 2.1.0 (2026-09-08)

- Release builds are uploaded with `uv publish` instead of twine.
- CI also tests Python 3.13.

## 2.0.0 (2026-07-14)

- Breaking: dropped Python 3.10. The minimum supported version is now 3.11.
- Breaking: the SDK uses httpx instead of requests.
- Breaking: `MAX_ATTEMPTS = 3` now counts the first request, so a failing call is retried at most twice (1.x retried three times after the first request).
- The default timeout is 10 seconds per request (was 30 seconds in 1.x).

## 1.8.0 (2026-02-03)

- Retry on HTTP 429, honouring the `Retry-After` header.

## 1.6.0 (2025-11-20)

- Formatting moved from black to ruff format.
