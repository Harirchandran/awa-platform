# Security Policy

## Project Maturity

- AWA is under active development.
- No public-production security certification is claimed.
- No uptime, compliance, or vulnerability-response SLA is provided.
- Deployments must be independently secured and reviewed.

## Supported Versions

| Version | Supported |
|---|---|
| `main` development branch | Best-effort security fixes |
| Tagged releases | No stable release published yet |
| Historical/private repository snapshots | Not supported |

## Reporting a Vulnerability

Do not open a public issue containing vulnerability details. After publication,
use GitHub private vulnerability reporting when the repository enables it.
Private vulnerability reporting must be enabled before the public release.

Until that channel is enabled, the repository is not ready to accept public
vulnerability disclosures. Do not include real credentials, personal data, or
third-party customer data in a report.

## What to Include

Include:

- The affected component.
- The affected commit or release.
- Reproduction conditions.
- The security impact.
- A minimal proof of concept.
- Suggested remediation when available.
- Whether the issue is already public.
- Redacted logs or screenshots only.

## Research Boundaries

This policy does not authorize:

- Access to third-party systems.
- Testing against real businesses or customers.
- Denial-of-service testing.
- Social engineering.
- Credential attacks.
- Privacy violations.
- Destructive testing.
- Testing systems not owned by the researcher.

## Response Process

The project will use this best-effort process without time guarantees:

1. Acknowledge through the private reporting channel.
2. Validate and classify.
3. Prepare a correction.
4. Coordinate disclosure where appropriate.
5. Publish an advisory or release when justified.

## Security Expectations for Deployers

Deployers must independently assess their environment and should:

- Rotate all secrets.
- Use a secret manager.
- Disable mock modes.
- Restrict CORS.
- Use HTTPS.
- Secure PostgreSQL and Redis.
- Configure provider webhooks safely.
- Review backups and retention.
- Enable monitoring and alerting.
- Run dependency and security checks.

These measures do not by themselves make a deployment secure or compliant.
