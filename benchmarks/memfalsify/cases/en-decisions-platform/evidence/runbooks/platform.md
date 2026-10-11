# Platform runbook

## Deploys

- Production deploys go through the canary stage first and need one approval from the owning team.
- Production deploys on Fridays are allowed until 14:00 UTC. After that, only hotfixes approved by the incident commander may ship.
- During the December freeze (2026-12-18 to 2027-01-04) only hotfixes ship.

## On-call

- Each platform on-call shift starts on Monday at 09:00 UTC and hands over to the next engineer the following Monday at 09:00 UTC.
- Page the on-call engineer when replica lag stays above 30 seconds for 5 minutes.
- Escalate to the secondary after 15 minutes without an acknowledgement.

## Legacy: RabbitMQ

- The RabbitMQ cluster has three nodes, one in each availability zone.
- Do not add new queues. New producers publish to MSK.
