# Contributing to AWA

## Project Status

AWA is under active development and is not yet represented as a verified
public-production system.

## Before Contributing

Before contributing:

- Read `CODE_OF_CONDUCT.md`.
- Read `SECURITY.md`.
- Search existing issues before opening a duplicate.
- Discuss substantial architectural or breaking changes before implementation.
- Do not submit secrets, real customer data, private tenant data, copied
  proprietary material, generated logs, browser traces, databases, archives,
  or unreviewed screenshots.

## Suitable Contributions

Suitable contributions include:

- Bug fixes.
- Tests.
- Documentation.
- Accessibility.
- Internationalization.
- Frontend usability.
- Provider adapters.
- Deployment documentation.
- Performance measurement.
- Security hardening.
- Reproducible developer tooling.

Security vulnerabilities must follow `SECURITY.md`, not public issue reporting.

## Development Setup

The future final `README.md` will contain the full development setup. For now,
the following commands are verified as present in the repository:

```text
npm run init
npm run dev
npm run test:backend
npm run lint
npm run build
```

Inspect `.env.example` before starting. Use only local or test credentials, and
never commit `.env` files.

## Branch and Pull-Request Workflow

1. Create a focused branch.
2. Make one coherent change.
3. Update tests and documentation when applicable.
4. Run applicable validation.
5. Open a pull request with a clear explanation.
6. Respond to review feedback.

## Coding and Change Standards

- Keep changes small and reviewable.
- Do not include unrelated refactors.
- Do not weaken tenant isolation, authentication, authorization, validation,
  idempotency, privacy, or security controls.
- Do not make unsupported production or compliance claims.
- Keep migration changes ordered and reversible where applicable.
- Document user-visible behavior changes.
- Add new configuration variables to `.env.example`.
- Do not add working credentials or weak production defaults.

## Testing Expectations

Contributors must identify which validation they ran. Applicable categories
include:

- Focused backend tests.
- Complete backend test suite.
- Frontend lint.
- Frontend build.
- Playwright tests.
- Compose configuration validation.
- Migration validation.
- Documentation-link validation.

## Commit Guidance

Use clear, imperative commit messages. Examples:

```text
fix: preserve active booking context
test: cover reminder retry behavior
docs: clarify local Redis configuration
feat: add provider adapter interface
```

## Licensing of Contributions

By submitting a contribution to AWA, you confirm that you created the
contribution or otherwise have the right to submit it, and you agree that the
contribution is licensed under AGPL-3.0-only.

No separate CLA is currently required.

## AI-Assisted Contributions

AI-assisted tools may be used, but contributors must:

- Personally review the resulting work.
- Understand the submitted changes.
- Verify that they have the right to submit the content.
- Disclose material AI assistance in the pull request.
- Run applicable validation.
- Remain responsible for correctness, security, privacy, licensing, and
  attribution.

Pull requests must disclose whether AI-assisted tools materially contributed,
which parts were assisted, and what human review and validation were performed.
Disclosure is not required for ordinary spelling completion or basic editor
autocomplete unless it materially generated the contribution.

## Security and Privacy Checklist

Confirm that:

- No secret was added.
- No personal or customer data was added.
- Screenshots are synthetic and reviewed.
- Logs and traces are redacted.
- Dependencies and copied assets have compatible licences.
- Public-facing claims are evidence-based.

## Review and Acceptance

Maintainers may request changes, reject out-of-scope work, or defer work that
conflicts with the project architecture or roadmap. Acceptance and response
times are not guaranteed.
