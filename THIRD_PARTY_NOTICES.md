# Third-Party Notices

## Purpose

AWA is licensed under AGPL-3.0-only. AWA uses third-party libraries distributed under their respective licences. This document is an informational inventory based on the committed lockfiles and locally available package metadata; it is not legal advice. Dependency licences remain governed by their original terms.

## Inventory Scope

The review covered `backend/requirements.txt`, `backend/requirements-dev.txt`, the root `package-lock.json`, `frontend/package-lock.json`, bundled frontend assets, and documentation assets. The Python review distinguishes the 35 runtime declarations from the two additional development packages; two runtime packages are also redeclared in the development requirements. Node roles follow the committed manifests. Transitive dependency graphs may change only when lockfiles change; Python transitive packages are not locked by this repository and therefore require a fresh installed-environment review for each release.

## Direct Python Dependencies

| Package | Declared version/range | Role | Declared licence | Evidence | Notes |
|---|---:|---|---|---|---|
| `fastapi` | `==0.138.0` | Direct runtime | MIT | Runtime requirement; local distribution metadata 0.138.0 | — |
| `uvicorn[standard]` | `==0.40.0` | Direct runtime | BSD-3-Clause | Runtime requirement; local distribution metadata 0.40.0 | Extras may add transitive packages. |
| `asyncpg` | `==0.31.0` | Direct runtime | Apache-2.0 | Runtime requirement; local distribution metadata 0.31.0 | — |
| `redis` | `==7.1.0` | Direct runtime | MIT | Runtime requirement; local distribution metadata 7.1.0 | — |
| `pydantic` | `==2.12.5` | Direct runtime | MIT | Runtime requirement; local distribution metadata 2.12.5 | — |
| `pydantic-settings` | `==2.14.2` | Direct runtime | MIT | Runtime requirement; local distribution metadata 2.14.2 | — |
| `email-validator` | `==2.3.0` | Direct runtime | Unlicense | Runtime requirement; local distribution metadata 2.3.0 | — |
| `requests` | `==2.33.0` | Direct runtime | Apache-2.0 | Runtime requirement; local distribution metadata 2.33.0 | — |
| `httpx` | `==0.28.1` | Direct runtime | BSD-3-Clause | Runtime requirement; local distribution metadata 0.28.1 | — |
| `python-multipart` | `==0.0.32` | Direct runtime | Apache-2.0 | Runtime requirement; local distribution metadata 0.0.32 | — |
| `PyJWT` | `==2.13.0` | Direct runtime | MIT | Runtime requirement; local distribution metadata 2.13.0 | — |
| `slowapi` | `==0.1.9` | Direct runtime | MIT | Runtime requirement; local distribution metadata 0.1.9 | — |
| `cryptography` | `==50.0.0` | Direct runtime | Apache-2.0 OR BSD-3-Clause | Runtime requirement; locally available metadata reports 49.0.0 | Exact 50.0.0 licence metadata was not locally installed. Manual verification required. |
| `bcrypt` | `==4.1.2` | Direct runtime | Apache-2.0 | Runtime requirement; local distribution metadata 4.1.2 | — |
| `openai` | `>=1.30.0` | Direct runtime | Apache-2.0 | Runtime requirement; local distribution metadata 2.38.0 | Range is not a lock. |
| `google-generativeai` | `>=0.7.0` | Direct runtime | Apache 2.0 | Runtime requirement; local distribution metadata 0.8.6 | Range is not a lock. |
| `jsonschema` | `>=4.20.0` | Direct runtime | MIT | Runtime requirement; local distribution metadata 4.26.0 | Range is not a lock. |
| `python-dotenv` | `==1.2.2` | Direct runtime | BSD-3-Clause | Runtime requirement; local distribution metadata 1.2.2 | — |
| `greenlet` | `==3.3.1` | Direct runtime | MIT AND Python-2.0 | Runtime requirement; local distribution metadata 3.3.1 | Compound expression retained as reported. |
| `qrcode` | `==8.2` | Direct runtime | BSD | Runtime requirement; local distribution metadata 8.2 | Metadata classifiers also include BSD and Other/Proprietary License. Manual verification required. |
| `python-dateutil` | `==2.9.0.post0` | Direct runtime | Dual License | Runtime requirement; local distribution metadata 2.9.0.post0 | Alternative classifiers are BSD and Apache. Manual verification required. |
| `langchain` | `>=1.3.11` | Direct runtime | MIT | Runtime requirement; local distribution metadata 1.3.11 | Range is not a lock. |
| `langchain-core` | `>=0.2.39` | Direct runtime | MIT | Runtime requirement; local distribution metadata 1.4.8 | Range is not a lock. |
| `langchain-openai` | `>=0.1.23` | Direct runtime | MIT | Runtime requirement; local distribution metadata 1.2.2 | Range is not a lock. |
| `langchain-groq` | `>=0.1.9` | Direct runtime | MIT | Runtime requirement; local distribution metadata 1.1.2 | Range is not a lock. |
| `langgraph` | `>=0.2.14` | Direct runtime | MIT | Runtime requirement; local distribution metadata 1.2.6 | Range is not a lock. |
| `setuptools` | `>=75.0.0` | Direct runtime | MIT | Runtime requirement; local distribution metadata 82.0.1 | Range is not a lock. |
| `razorpay` | `==1.4.1` | Direct runtime | MIT | Runtime requirement; local distribution metadata 1.4.1 | — |
| `idna` | `>=3.15` | Direct runtime | BSD-3-Clause | Runtime requirement; local distribution metadata 3.16 | Range is not a lock. |
| `pillow` | `>=12.2.0` | Direct runtime | MIT-CMU | Runtime requirement; local distribution metadata 12.2.0 | Range is not a lock. |
| `pygments` | `>=2.20.0` | Direct runtime | BSD-2-Clause | Runtime requirement; local distribution metadata 2.20.0 | Range is not a lock. |
| `pypdf` | `>=6.14.2` | Direct runtime | BSD-3-Clause | Runtime requirement; local distribution metadata 6.14.2 | Range is not a lock. |
| `urllib3` | `>=2.7.0` | Direct runtime | MIT | Runtime requirement; local distribution metadata 2.7.0 | Range is not a lock. |
| `langgraph-checkpoint` | `>=4.1.1`; dev redeclaration `>=4.1.1` | Direct runtime and development | MIT | Both requirements; local distribution metadata 4.1.1 | Range is not a lock. |
| `langsmith` | `>=0.9.1`; dev redeclaration `>=0.8.0` | Direct runtime and development | MIT | Both requirements; local distribution metadata 0.9.1 | Runtime constraint is stricter; range is not a lock. |
| `pytest` | `==9.0.3` | Direct development | MIT | Development requirement; local distribution metadata 9.0.3 | — |
| `pytest-asyncio` | `==1.3.0` | Direct development | Apache-2.0 | Development requirement; local distribution metadata 1.3.0 | — |

## Direct Root Node Dependencies

| Package | Declared version/range | Role | Declared licence | Evidence | Notes |
|---|---:|---|---|---|---|
| `@mermaid-js/mermaid-cli` | `^11.16.0` | Direct development | MIT | Root manifest and lockfile metadata, resolved 11.16.0 | — |
| `playwright` | `^1.61.0` | Direct development | Apache-2.0 | Root manifest and lockfile metadata, resolved 1.61.0 | — |

## Direct Frontend Dependencies

| Package | Declared version/range | Role | Declared licence | Evidence | Notes |
|---|---:|---|---|---|---|
| `@hookform/resolvers` | `^5.2.2` | Direct runtime | MIT | Frontend manifest and lockfile, 5.2.2 | — |
| `@monaco-editor/react` | `^4.7.0` | Direct runtime | MIT | Frontend manifest and lockfile, 4.7.0 | — |
| `@radix-ui/react-dialog` | `^1.1.15` | Direct runtime | MIT | Frontend manifest and lockfile, 1.1.15 | — |
| `@radix-ui/react-dropdown-menu` | `^2.1.16` | Direct runtime | MIT | Frontend manifest and lockfile, 2.1.16 | — |
| `@radix-ui/react-label` | `^2.1.8` | Direct runtime | MIT | Frontend manifest and lockfile, 2.1.8 | — |
| `@radix-ui/react-popover` | `^1.1.15` | Direct runtime | MIT | Frontend manifest and lockfile, 1.1.15 | — |
| `@radix-ui/react-scroll-area` | `^1.2.10` | Direct runtime | MIT | Frontend manifest and lockfile, 1.2.10 | — |
| `@radix-ui/react-select` | `^2.2.6` | Direct runtime | MIT | Frontend manifest and lockfile, 2.2.6 | — |
| `@radix-ui/react-slot` | `^1.2.4` | Direct runtime | MIT | Frontend manifest and lockfile, 1.2.4 | — |
| `@radix-ui/react-switch` | `^1.2.6` | Direct runtime | MIT | Frontend manifest and lockfile, 1.2.6 | — |
| `@radix-ui/react-tabs` | `^1.1.13` | Direct runtime | MIT | Frontend manifest and lockfile, 1.1.13 | — |
| `@radix-ui/react-tooltip` | `^1.2.10` | Direct runtime | MIT | Frontend manifest and lockfile, 1.2.10 | — |
| `@tailwindcss/typography` | `^0.5.20` | Direct runtime | MIT | Frontend manifest and lockfile, 0.5.20 | — |
| `@tanstack/react-query` | `^5.99.0` | Direct runtime | MIT | Frontend manifest and lockfile, 5.99.0 | — |
| `@tanstack/react-table` | `^8.21.3` | Direct runtime | MIT | Frontend manifest and lockfile, 8.21.3 | — |
| `@tanstack/react-virtual` | `^3.14.4` | Direct runtime | MIT | Frontend manifest and lockfile, 3.14.4 | — |
| `@types/uuid` | `^10.0.0` | Direct runtime | MIT | Frontend manifest and lockfile, 10.0.0 | Type declarations are in the runtime section. |
| `axios` | `^1.18.0` | Direct runtime | MIT | Frontend manifest and lockfile, 1.19.0 | — |
| `class-variance-authority` | `^0.7.1` | Direct runtime | Apache-2.0 | Frontend manifest and lockfile, 0.7.1 | — |
| `clsx` | `^2.1.1` | Direct runtime | MIT | Frontend manifest and lockfile, 2.1.1 | — |
| `date-fns` | `^4.1.0` | Direct runtime | MIT | Frontend manifest and lockfile, 4.1.0 | — |
| `framer-motion` | `^12.38.0` | Direct runtime | MIT | Frontend manifest and lockfile, 12.38.0 | — |
| `lucide-react` | `^1.8.0` | Direct runtime | ISC | Frontend manifest and lockfile, 1.8.0 | Dependency-provided icons remain third-party components. |
| `react` | `19.2.7` | Direct runtime | MIT | Frontend manifest and lockfile, 19.2.7 | — |
| `react-day-picker` | `^9.14.0` | Direct runtime | MIT | Frontend manifest and lockfile, 9.14.0 | — |
| `react-dom` | `19.2.7` | Direct runtime | MIT | Frontend manifest and lockfile, 19.2.7 | — |
| `react-error-boundary` | `^6.1.2` | Direct runtime | MIT | Frontend manifest and lockfile, 6.1.2 | — |
| `react-hook-form` | `^7.72.1` | Direct runtime | MIT | Frontend manifest and lockfile, 7.72.1 | — |
| `react-hot-toast` | `^2.6.0` | Direct runtime | MIT | Frontend manifest and lockfile, 2.6.0 | — |
| `react-is` | `^19.2.7` | Direct runtime | MIT | Frontend manifest and lockfile, 19.2.7 | — |
| `react-markdown` | `^10.1.0` | Direct runtime | MIT | Frontend manifest and lockfile, 10.1.0 | — |
| `react-router` | `8.3.0` | Direct runtime | MIT | Frontend manifest and lockfile, 8.3.0 | `react-router-dom` is not a current direct dependency. |
| `recharts` | `^3.8.1` | Direct runtime | MIT | Frontend manifest and lockfile, 3.8.1 | — |
| `remark-breaks` | `^4.0.0` | Direct runtime | MIT | Frontend manifest and lockfile, 4.0.0 | — |
| `remark-gfm` | `^4.0.1` | Direct runtime | MIT | Frontend manifest and lockfile, 4.0.1 | — |
| `tailwind-merge` | `^3.5.0` | Direct runtime | MIT | Frontend manifest and lockfile, 3.5.0 | — |
| `tailwindcss-animate` | `^1.0.7` | Direct runtime | MIT | Frontend manifest and lockfile, 1.0.7 | — |
| `uuid` | `^13.0.0` | Direct runtime | MIT | Frontend manifest and lockfile, 13.0.2 | — |
| `zod` | `^4.3.6` | Direct runtime | MIT | Frontend manifest and lockfile, 4.3.6 | — |
| `zustand` | `^5.0.12` | Direct runtime | MIT | Frontend manifest and lockfile, 5.0.12 | — |
| `@emnapi/core` | `^1.10.0` | Direct development | MIT | Frontend manifest and lockfile, 1.11.1 | — |
| `@emnapi/runtime` | `^1.10.0` | Direct development | MIT | Frontend manifest and lockfile, 1.11.1 | — |
| `@eslint/js` | `^9.39.4` | Direct development | MIT | Frontend manifest and lockfile, 9.39.4 | — |
| `@playwright/test` | `^1.61.0` | Direct development | Apache-2.0 | Frontend manifest and lockfile, 1.61.0 | — |
| `@types/node` | `^24.12.2` | Direct development | MIT | Frontend manifest and lockfile, 24.12.2 | — |
| `@types/react` | `^19.2.14` | Direct development | MIT | Frontend manifest and lockfile, 19.2.14 | — |
| `@types/react-dom` | `^19.2.3` | Direct development | MIT | Frontend manifest and lockfile, 19.2.3 | — |
| `@vitejs/plugin-react` | `^6.0.1` | Direct development | MIT | Frontend manifest and lockfile, 6.0.1 | — |
| `autoprefixer` | `^10.5.0` | Direct development | MIT | Frontend manifest and lockfile, 10.5.0 | — |
| `eslint` | `^9.39.4` | Direct development | MIT | Frontend manifest and lockfile, 9.39.4 | — |
| `eslint-plugin-react-hooks` | `^7.0.1` | Direct development | MIT | Frontend manifest and lockfile, 7.1.1 | — |
| `eslint-plugin-react-refresh` | `^0.5.2` | Direct development | MIT | Frontend manifest and lockfile, 0.5.2 | — |
| `globals` | `^17.4.0` | Direct development | MIT | Frontend manifest and lockfile, 17.5.0 | — |
| `playwright` | `^1.60.0` | Direct development | Apache-2.0 | Frontend manifest and lockfile, 1.61.0 | — |
| `postcss` | `^8.5.23` | Direct development | MIT | Frontend manifest and lockfile, 8.5.25 | — |
| `tailwindcss` | `^3.4.19` | Direct development | MIT | Frontend manifest and lockfile, 3.4.19 | — |
| `typescript` | `~6.0.2` | Direct development | Apache-2.0 | Frontend manifest and lockfile, 6.0.3 | — |
| `typescript-eslint` | `^8.58.0` | Direct development | MIT | Frontend manifest and lockfile, 8.58.2 | — |
| `vite` | `^8.0.4` | Direct development | MIT | Frontend manifest and lockfile, 8.1.0 | — |

## Dependencies Requiring Specific Review

| Package | Resolved version | Reported licence | Why review is needed | Disposition |
|---|---:|---|---|---|
| `khroma` | 2.1.0 | MIT | Root transitive lockfile metadata omits a licence field, while the installed package includes an MIT licence file. | Attribution retained through package distribution |
| `python-dateutil` | 2.9.0.post0 | Dual License | Installed metadata reports “Dual License” and BSD and Apache classifiers; no alternative is selected here. | Manual verification required before packaged binary distribution |
| `certifi` | 2026.1.4 | MPL-2.0 | Python transitive dependency; the repository has no Python transitive lockfile, so the version is from the locally reviewed environment. | Licence metadata confirmed |
| `orjson` | 3.11.9 | MPL-2.0 AND (Apache-2.0 OR MIT) | Installed metadata reports a compound expression and the package can include compiled code. | Manual verification required before packaged binary distribution |
| `tqdm` | 4.67.3 | MPL-2.0 AND MIT | Installed metadata reports a compound expression; the repository has no Python transitive lockfile. | Manual verification required before packaged binary distribution |
| `dompurify` | 3.4.13 | (MPL-2.0 OR Apache-2.0) | Transitive in both Node lockfiles; the alternative expression is retained without selecting a term. | Licence metadata confirmed |
| `elkjs` | 0.9.3 | EPL-2.0 | Root transitive dependency used by the documentation toolchain. | Not redistributed as a copied source asset |
| `lightningcss` | 1.32.0 | MPL-2.0 | Frontend transitive package with platform-specific native packages retained in the lockfile. | Manual verification required before packaged binary distribution |
| `lightningcss-android-arm64` | 1.32.0 | MPL-2.0 | Optional retained platform package. | Manual verification required before packaged binary distribution |
| `lightningcss-darwin-arm64` | 1.32.0 | MPL-2.0 | Optional retained platform package. | Manual verification required before packaged binary distribution |
| `lightningcss-darwin-x64` | 1.32.0 | MPL-2.0 | Optional retained platform package. | Manual verification required before packaged binary distribution |
| `lightningcss-freebsd-x64` | 1.32.0 | MPL-2.0 | Optional retained platform package. | Manual verification required before packaged binary distribution |
| `lightningcss-linux-arm-gnueabihf` | 1.32.0 | MPL-2.0 | Optional retained platform package. | Manual verification required before packaged binary distribution |
| `lightningcss-linux-arm64-gnu` | 1.32.0 | MPL-2.0 | Optional retained platform package. | Manual verification required before packaged binary distribution |
| `lightningcss-linux-arm64-musl` | 1.32.0 | MPL-2.0 | Optional retained platform package. | Manual verification required before packaged binary distribution |
| `lightningcss-linux-x64-gnu` | 1.32.0 | MPL-2.0 | Optional retained platform package. | Manual verification required before packaged binary distribution |
| `lightningcss-linux-x64-musl` | 1.32.0 | MPL-2.0 | Optional retained platform package. | Manual verification required before packaged binary distribution |
| `lightningcss-win32-arm64-msvc` | 1.32.0 | MPL-2.0 | Optional retained platform package. | Manual verification required before packaged binary distribution |
| `lightningcss-win32-x64-msvc` | 1.32.0 | MPL-2.0 | Optional retained platform package. | Manual verification required before packaged binary distribution |

These are dependency packages, not copied project-authored assets. Their licence and attribution files remain part of their package distributions. This inventory makes no definitive legal-compatibility conclusion.

## Security Status

The final Python dependency audit reported no known vulnerabilities. Root and frontend npm audits reported no Critical, High, Moderate, or Low findings at commit `6153123ac6763bede7a5b8c5509031ecf50371db`. Audit status is time-sensitive and must be rechecked for future releases.

## Redistribution Guidance

Release maintainers must retain applicable copyright and licence notices; review packaged Docker images and frontend bundles; update this inventory when lockfiles change; verify licences for newly added copied source, fonts, icons, datasets, models, or media; and avoid copying third-party material without recorded origin and licence.

## Project Licence

See [LICENSE](LICENSE). AWA project source is licensed under AGPL-3.0-only, while third-party components remain under their respective licences.
