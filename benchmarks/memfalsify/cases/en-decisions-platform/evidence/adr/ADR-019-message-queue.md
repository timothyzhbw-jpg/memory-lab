# ADR-019: Replace RabbitMQ

- Status: Accepted
- Date: 2026-06-02
- Owner: Marcus Oyelaran

## Context

RabbitMQ caused three incidents this year, all from unbounded queue growth while consumers were down. We need a replacement with durable topics that consumers can replay.

## Options

- Option A: self-hosted Kafka on EC2.
- Option B: Amazon MSK (managed Kafka), provisioned with our existing Terraform modules.
- Option C: keep RabbitMQ and switch to quorum queues.

## Decision

Option A (self-hosted Kafka on EC2) was rejected: nobody on the team has run Kafka in production, and we cannot staff a broker on-call.
Option C was rejected because quorum queues still give us no replay.
We go with Option B and decommission RabbitMQ once the last consumer has moved.

## Notes

Broker count and topic retention for MSK are tracked in PLAT-412 and are not part of this decision.
