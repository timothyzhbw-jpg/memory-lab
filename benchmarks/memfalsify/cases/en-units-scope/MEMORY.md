# Northline capacity memory

<!-- Synthetic benchmark data: Northline is a fictional company. -->

## Latency and errors

- Search p99 must stay within a quarter of a second.
- The search latency budget at the 99th percentile is 250 milliseconds.
- The billing service's p99 latency budget is a quarter of a second.
- A checkout write may take up to 2 minutes to return.
- The monthly error budget for search is one tenth of one percent of requests.
- The search service may fail 1 percent of requests each month.
- The billing service is probably allowed a smaller share of failed requests each month than search.

## Storage

- The hot tier holds half a tebibyte of search indexes.
- Search indexes on the hot tier take up 512 GiB.
- Search indexes occupy 512 GB on the hot tier.
- The billing archive holds 500 GiB.
- Hot-tier retention for search indexes is 90 days.
- Hot-tier search indexes are kept for 30 days.
- 2026-03-02: the capacity group planned to keep hot-tier search indexes for 30 days.

## Timeouts, retries and releases

- The gateway timeout for billing invoice renders is 1500 ms.
- Every gateway timeout is 1500 ms.
- 105 days passed between the production debut of release 2.3.1, on 2026-05-20, and the replacement by release 2.4.0.
- Only 30 days passed between the production debut of release 2.3.1, on 2026-05-20, and the replacement by release 2.4.0.
- Production is one patch release ahead of release 2.3.1.
- A search query that returned HTTP 200 must not be retried.
- Clients no longer retry search queries after HTTP 200.
- Billing invoice renders must not be retried.
- Retrying a billing invoice render is permitted solely when the first try responded with HTTP 503.
