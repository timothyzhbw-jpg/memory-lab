# Platform sync, 2026-09-03

Attendees: Hannah Ilves, Marcus Oyelaran, Tomás Ferreira, Sven Aaltonen

## Decisions

- Ownership of the MSK migration moves from Marcus Oyelaran to Hannah Ilves, effective today, because Marcus is joining the data team.
- Lower the per-key API limit from 1,200 to 600 requests per minute, effective 2026-09-08, after last week's scraping incident.
- Tier-1 services must move their secrets to Vault by 2026-12-31, and tier-2 services have until 2027-03-31.

## Notes

- Marcus stays available for MSK questions until the end of September.
- The per-IP limit is unchanged.
- Tomás confirmed the Postgres 16 upgrade for the 2026-09-14 maintenance window.
