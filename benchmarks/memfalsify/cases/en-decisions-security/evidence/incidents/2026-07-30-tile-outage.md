# Postmortem: map tiles unavailable (2026-07-30)

Status: final. Author: Hannah Ilves.

## Summary

Map tiles failed to load for 47 minutes (14:02 to 14:49 UTC). Route planning kept working with cached tiles.

## Analysis

Root cause: a Terraform change deleted the DNS record for tiles.internal, so the tile proxy could not reach the tile servers.

## Timeline

- 14:02 First 5xx errors from the tile proxy.
- 14:09 On-call paged.
- 14:21 Investigated an expired TLS certificate on the staging tile server; it was unrelated to the outage.
- 14:44 DNS record restored from Terraform state.
- 14:49 Error rate back to baseline.

## Follow-ups

- Require a second reviewer for any Terraform plan that deletes DNS records.
- Status page updates within 15 minutes of the first page.
