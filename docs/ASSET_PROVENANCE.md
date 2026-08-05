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

All six files exist and first appear together in the sanitized baseline commit. The current README identifies the interface captures as synthetic demonstrations and the architecture material as explanatory documentation. Git history does not include source files, generation commands, licence annotations, or a conclusive creation record for any PNG. Accordingly, each remains **Provenance not fully established — author confirmation required**.

| Asset | Classification | Origin evidence | Data classification | Publication status | Notes |
|---|---|---|---|---|---|
| `docs/assets/software-architecture.png` | Provenance not fully established — author confirmation required | Project-specific architecture content; added in sanitized baseline; no generator/source record | Technical diagram; no customer, payment, contact, tenant, credential, or personal data | Approved after author confirmation | Confirm that it was project-authored or generated under the author’s direction and is publishable. |
| `docs/assets/whatsapp-message-sequence.png` | Provenance not fully established — author confirmation required | Project-specific message-flow content; added in sanitized baseline; no generator/source record | Technical diagram; no customer, payment, contact, tenant, credential, or personal data | Approved after author confirmation | Confirm that it was project-authored or generated under the author’s direction and is publishable. |
| `docs/assets/system-components.png` | Provenance not fully established — author confirmation required | Project-specific component diagram; added in sanitized baseline; no generator/source record | Technical diagram using synthetic system labels; no private data | Approved after author confirmation | Confirm that it was project-authored or generated under the author’s direction and is publishable. |
| `docs/assets/tenant-dashboard-demo.png` | Provenance not fully established — author confirmation required | Repository-described synthetic local demonstration capture; added in sanitized baseline | Synthetic demonstration data; no actual customer, payment, contact, tenant, credential, or personal data | Approved after author confirmation | Not evidence of a public production deployment. Confirm capture ownership and publication permission. |
| `docs/assets/public-chat-demo.png` | Provenance not fully established — author confirmation required | Repository-described synthetic local demonstration capture; added in sanitized baseline | Synthetic demonstration data; no actual customer, payment, contact, tenant, credential, or personal data | Approved after author confirmation | Not evidence of a public production deployment. Confirm capture ownership and publication permission. |
| `docs/assets/audit-logs-demo.png` | Provenance not fully established — author confirmation required | Repository-described synthetic local demonstration capture; added in sanitized baseline | Synthetic demonstration data; no actual customer, payment, contact, tenant, credential, or personal data | Approved after author confirmation | Not evidence of a public production deployment. Confirm capture ownership and publication permission. |

No embedded image metadata was detected during validation.

## Frontend Public Assets

| Asset | Classification | Origin evidence | Data classification | Publication status | Notes |
|---|---|---|---|---|---|
| `frontend/public/favicon.svg` | Provenance not fully established — replace or confirm before publication | Single compressed SVG with no comment, author, licence, or source metadata; its lightning-mark structure suggests an unidentified template/tool origin | No personal or private data | Replace before publication | Do not infer an icon source. Retain only if the author records the exact origin and redistribution basis. |
| `frontend/public/icons.svg` | Derived or adapted; provenance not fully established — replace or confirm before publication | SVG sprite names several third-party platform brands plus generic symbols, but contains no source, author, licence, or attribution comments | No personal or private data | Replace before publication | Record the exact source and licence for every retained glyph, or replace the sprite with documented assets. |

Repository history shows both SVGs arriving in the sanitized baseline and supplies no earlier provenance. No reliable origin evidence establishes either as project-authored.

## Dependency-Provided Visual Assets

Visual assets and components delivered through installed libraries are distinct from copied tracked assets. Dependency-provided icons and components remain governed by the dependency’s licence and do not become project-authored assets merely because they render in the UI. The committed frontend manifest includes `lucide-react`; its delivered icons are dependency assets under the licence reported in the lockfile, not copied project artwork.

## Privacy Review

The six reviewed documentation images contain synthetic demonstration data or technical diagrams. The visual and metadata review found no personal email, real phone number, credential, private tenant information, actual payment record, local filesystem path, or embedded metadata. The screenshots are not evidence of a public production deployment. This document intentionally does not reproduce names, phone-like values, or records visible in the captures.

## Publication Decision

- **Approved for publication:** no tracked asset is placed in this category while creation provenance remains unresolved.
- **Approved after author confirmation:** the six documentation PNGs, after the author confirms their creation provenance and publication rights.
- **Replace before publication:** `frontend/public/favicon.svg` and `frontend/public/icons.svg`, unless exact source and redistribution evidence is recorded before publication.
- **Exclude from publication:** none based on the reviewed repository evidence.

No asset with unresolved external ownership is marked fully approved.

## Maintainer Process

Future contributors adding an asset must record its filename, creator or source, creation date when known, licence, modifications, whether AI assistance was used materially, privacy-review result, and publication approval. If a source or right cannot be established, the asset must remain unapproved or be replaced before publication.
