# Encryption standard

Owner: Sven Aaltonen.

- Production databases, backups and object storage are encrypted at rest with keys managed in the cloud provider's KMS.
- Encryption keys are rotated every 365 days.
- External traffic uses TLS 1.2 or later. Internal service-to-service traffic uses mutual TLS.
