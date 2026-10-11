# ADR-021: Move the primary cluster to PostgreSQL 16

- Status: Accepted
- Date: 2026-08-18
- Supersedes: ADR-014
- Owner: Tomás Ferreira

## Context

ADR-014 planned to stay on PostgreSQL 15 through 2026. Logical replication in 16 lets us retire the custom change-data-capture sidecar, and running that sidecar for another year costs more than upgrading now.

## Decision

Upgrade the primary cluster from 15 to 16 in the September maintenance window. The cluster keeps three read replicas, one per availability zone.

## Rollback

Keep the final 15 snapshot for 14 days after the upgrade.
