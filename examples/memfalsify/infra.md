---
name: infra
description: Gateway, database and deploy facts for checkout-service
---

- The public API rate limit is 100 requests per minute.
- The main database runs PostgreSQL 15.
- Deployments always go through the staging cluster.
- Bulk export depends on the billing schema migration.
