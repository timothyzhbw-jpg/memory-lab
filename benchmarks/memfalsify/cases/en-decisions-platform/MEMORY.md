# Platform memory

<!-- Synthetic benchmark data: Corvane is a fictional company. -->

## Data stores

- The primary database runs PostgreSQL 15.
- Writes were unavailable for roughly 11 minutes during the Postgres 16 upgrade.
- Production database backups run every 6 hours and are kept for 35 days.
- The staging database is backed up every 6 hours.
- [Database notes](database.md) — version and replica details

## Messaging

- Self-hosting Kafka on EC2 was approved as the replacement for RabbitMQ.
- We picked Amazon's managed Kafka service to replace RabbitMQ.
- Marcus Oyelaran owns the MSK migration.
- Hannah Ilves is the DRI for the move from RabbitMQ to MSK.
- The MSK cluster runs in three availability zones.
- Kafka topics on MSK keep messages for 7 days.

## Gateway, deploys and on-call

- The public API allows 1,200 requests per minute per API key.
- Per-client-IP traffic is capped at 1200 requests per minute.
- Production deploys are never allowed on Fridays.
- Platform on-call shifts last one week.
- Every service must move to Vault by 2026-12-31.
- Datadog was recently approved as the logging vendor.
