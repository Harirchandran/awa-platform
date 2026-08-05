# AWA Asset Provenance

## Purpose

This document records the origin evidence, privacy classification, and publication review status of tracked non-code visual assets. It reports what the sanitized repository and its history establish; it does not infer ownership where the evidence is incomplete.

## Provenance Classifications

- **Project-authored:** created by the project author or contributors, with repository evidence supporting that origin.
- **Generated under author direction:** produced with a tool or assisted workflow under a human author’s direction. The tool is not described as the copyright owner.
- **Third-party dependency asset:** delivered by an installed dependency and governed by that dependency’s terms.
- **Derived or adapted:** modified from an identified external or earlier work, requiring source and licence records.
- **Provenance not fully established:** the repository does not conclusively establish the creator, generation method, or external source.

## Reviewed Documentation Assets

All six files exist and first appear together in the sanitized baseline commit. The current README identifies the interface captures as synthetic demonstrations and the architecture material as explanatory documentation. On 2026-08-05, Hari R Chandran confirmed that all six files were created by him or generated under his direction specifically for AWA, contain synthetic demonstration data, and may be publicly distributed with the project. The exact creation method cannot be distinguished from the available records, so each is classified as **Project-authored or generated under author direction**. This confirmation does not imply institutional ownership or endorsement.

| Asset | Classification | Origin evidence | Data classification | Publication status | Notes |
|---|---|---|---|---|---|
| `docs/assets/software-architecture.png` | Project-authored or generated under author direction | Author confirmation received 2026-08-05; project-specific architecture content added in sanitized baseline | Technical diagram; no customer, payment, contact, tenant, credential, or personal data | Approved for publication | Confirmed as created for AWA and publicly distributable with the project. |
| `docs/assets/whatsapp-message-sequence.png` | Project-authored or generated under author direction | Author confirmation received 2026-08-05; project-specific message-flow content added in sanitized baseline | Technical diagram; no customer, payment, contact, tenant, credential, or personal data | Approved for publication | Confirmed as created for AWA and publicly distributable with the project. |
| `docs/assets/system-components.png` | Project-authored or generated under author direction | Author confirmation received 2026-08-05; project-specific component diagram added in sanitized baseline | Technical diagram using synthetic system labels; no private data | Approved for publication | Confirmed as created for AWA and publicly distributable with the project. |
| `docs/assets/tenant-dashboard-demo.png` | Project-authored or generated under author direction | Author confirmation received 2026-08-05; repository-described synthetic local demonstration capture | Synthetic demonstration data; no actual customer, payment, contact, tenant, credential, or personal data | Approved for publication | Not evidence of a public production deployment; confirmed as publicly distributable with AWA. |
| `docs/assets/public-chat-demo.png` | Project-authored or generated under author direction | Author confirmation received 2026-08-05; repository-described synthetic local demonstration capture | Synthetic demonstration data; no actual customer, payment, contact, tenant, credential, or personal data | Approved for publication | Not evidence of a public production deployment; confirmed as publicly distributable with AWA. |
| `docs/assets/audit-logs-demo.png` | Project-authored or generated under author direction | Author confirmation received 2026-08-05; repository-described synthetic local demonstration capture | Synthetic demonstration data; no actual customer, payment, contact, tenant, credential, or personal data | Approved for publication | Not evidence of a public production deployment; confirmed as publicly distributable with AWA. |

No embedded image metadata was detected during validation.

## Frontend Public Assets

| Asset | Classification | Origin evidence | Data classification | Publication status | Notes |
|---|---|---|---|---|---|
| `frontend/public/favicon.svg` | Project-authored | Original AWA monogram created from basic SVG geometry during open-source publication preparation; no third-party source used | No personal or private data | Approved for publication | No external licence or attribution required. |
| `frontend/public/icons.svg` | Project-authored | Original generic AWA interface-icon sprite created from basic SVG geometry during open-source publication preparation; no third-party source used | No personal or private data | Approved for publication | No external licence or attribution required. |

These publication-preparation replacements supersede the earlier SVG files of uncertain provenance. They contain no retained third-party paths, brand marks, or provider logos.

## Dependency-Provided Visual Assets

Visual assets and components delivered through installed libraries are distinct from copied tracked assets. Dependency-provided icons and components remain governed by the dependency’s licence and do not become project-authored assets merely because they render in the UI. The committed frontend manifest includes `lucide-react`; its delivered icons are dependency assets under the licence reported in the lockfile, not copied project artwork.

## Privacy Review

The six reviewed documentation images contain synthetic demonstration data or technical diagrams. The visual and metadata review found no personal email, real phone number, credential, private tenant information, actual payment record, local filesystem path, or embedded metadata. The screenshots are not evidence of a public production deployment. This document intentionally does not reproduce names, phone-like values, or records visible in the captures.

## Publication Decision

- **Approved for publication:** the six author-confirmed documentation PNGs and the two project-authored replacement SVGs.
- **Approved after author confirmation:** none; the required confirmation was received on 2026-08-05.
- **Replace before publication:** none; both SVGs of uncertain provenance were replaced during publication preparation.
- **Exclude from publication:** none based on the reviewed repository evidence.

No asset with unresolved external ownership is marked fully approved.

## Maintainer Process

Future contributors adding an asset must record its filename, creator or source, creation date when known, licence, modifications, whether AI assistance was used materially, privacy-review result, and publication approval. If a source or right cannot be established, the asset must remain unapproved or be replaced before publication.
