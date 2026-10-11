# Platform sync, 2026-09-17

Attendees: Hannah Ilves, Tomás Ferreira, Sven Aaltonen, Kenji Mori

## Updates

- Postgres 16 upgrade of the primary cluster finished on 2026-09-14, with writes blocked for 11 minutes during the cutover.
- MSK migration: 9 of 14 consumers have moved. Hannah expects the rest to move in October.
- Logging vendor: Datadog and Grafana Cloud quotes are in. Sven will compare them against the current Elastic bill before anything is signed.

## Action items

- Tomás: delete the PostgreSQL 15 rollback snapshot on 2026-09-28.
- Kenji: update the status page template for the October maintenance window.
