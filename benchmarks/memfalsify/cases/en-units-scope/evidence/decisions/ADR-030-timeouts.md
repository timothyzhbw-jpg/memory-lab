# ADR-030 Request timeouts

Accepted on 2026-07-22, this record supersedes ADR-018.

The gateway timeout for search queries is 1500 ms.

The gateway timeout for billing invoice renders is 8 seconds.

Clients must not retry a search query that already returned HTTP 200.

A billing invoice render may be retried at most once, and only if the first attempt returned HTTP 503.

Until 2026-07-22, clients retried search queries even after HTTP 200.
