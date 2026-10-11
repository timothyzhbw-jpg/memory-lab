# Database backups

Owner: Tomás Ferreira. Last reviewed on 2026-09-20.

## Production

- Production snapshots are taken every 6 hours and kept for 14 days.
- A full backup is taken every Sunday at 02:00 UTC and kept for 35 days.
- Restores are tested on the first Tuesday of each month.

## Staging

- Staging is rebuilt every night from an anonymized copy of the latest production snapshot.
