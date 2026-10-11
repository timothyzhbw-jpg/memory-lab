# ADR-014: Primary database version

- Status: Superseded by ADR-021 (2026-08-18)
- Date: 2026-03-12
- Owner: Tomás Ferreira

## Context

The primary cluster still runs PostgreSQL 13, and our managed-database provider ends support for it this year.

## Decision

Upgrade the primary cluster to PostgreSQL 15 and stay on 15 until at least the end of 2026.

## Consequences

Every extension we use must be re-tested against 15. The upgrade needs a maintenance window of up to 30 minutes.
