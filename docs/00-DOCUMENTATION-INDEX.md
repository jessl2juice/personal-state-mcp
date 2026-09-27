# Personal State Documentation Set

Document version: 1.0  
Product status: Production pilot  
Last verified: 2026-09-27
Document owner: Personal State project

## Purpose

Personal State is a private, read-only physiological context system. It combines glucose from a FreeStyle Libre 3 Plus follower connection with heart-rate and selected Samsung Health data from a Galaxy Watch5 Pro and Android phone. It preserves long-term history, presents the data on a shared timeline, and gives authorized AI agents selective access through Model Context Protocol (MCP).

The system is designed to help the user and the user's clinicians review recorded facts with clear timestamps, freshness, gaps, provenance, and source limitations. It is not an alarm system, diagnostic system, treatment recommender, emergency service, or medical device. Official Libre and Samsung applications remain the safety and device-authority layer.

## Current release

| Component | Version | Role |
| --- | --- | --- |
| Personal State Python service | 0.4.0 | Collection, normalized storage, dashboard, MCP, exports |
| Android phone companion | 0.4.1 (code 40100) | Full authorized-history import, Health Connect and Samsung reads, watch relay, authenticated upload, reconnect recovery |
| Galaxy Watch companion | 0.4.1 (code 40101) | Five-second direct live heart-rate delivery, phone relay, and post-reboot recovery |
| Dashboard | Private production pilot | User and clinician review at the protected project hostname |

## Read this first

1. [User and Clinician Guide](01-USER-AND-CLINICIAN-GUIDE.md) explains what the dashboard shows, how to interpret timestamps and gaps, and how different clinicians can use it.
2. [Installation and Operations Guide](02-INSTALLATION-AND-OPERATIONS.md) covers setup, daily operation, backup, export, service health, and planned maintenance.
3. [Architecture, Security, and Privacy](03-ARCHITECTURE-SECURITY-PRIVACY.md) documents the data flow, trust boundaries, controls, threat model, and residual risks.
4. [API and Data Reference](04-API-AND-DATA-REFERENCE.md) defines the MCP tools, HTTP routes, normalized records, units, freshness states, and threshold semantics.
5. [Troubleshooting Runbook](05-TROUBLESHOOTING-RUNBOOK.md) provides symptom-first recovery procedures, including loss of live heart rate.
6. [Validation, Release, and Governance](06-VALIDATION-RELEASE-AND-GOVERNANCE.md) records the review gates, test plan, release checklist, change rules, and known limitations.
7. [Distribution and Installer](07-DISTRIBUTION-AND-INSTALLER.md) defines release packaging, signing, update, and verification requirements.
8. [Installer Quick Start](INSTALLER-QUICK-START.md) is the concise setup guide for a new user.
9. [Historical Backfill](08-HISTORICAL-BACKFILL.md) covers LibreView, Google Fit Takeout, and Samsung Health archive imports with validation and deduplication requirements.

The original detailed design, independent Fable review, resolution logs, and normative watch-ingest schema remain part of the engineering record:

- [Detailed design](design.md)
- [Galaxy Watch5 Pro design addendum](watch5-pro-design-addendum.md)
- [Fable review](review/fable-review.md)
- [Fable Watch5 Pro review](review/fable-watch5-pro-review.md)
- [Resolution log](review/resolution-log.md)
- [Watch5 Pro resolution log](review/watch5-pro-resolution-log.md)
- [Watch ingest schema v1](watch-ingest-schema-v1.json)
- [Samsung Health Data adapter design](samsung-health-data-adapter-design.md)
- [Samsung Health Data review resolution log](review/samsung-health-data-resolution-log.md)
- [Watch ingest schema v2](watch-ingest-schema-v2.json)

## Audience map

| Reader | Start with | Then read |
| --- | --- | --- |
| Person whose data is shown | User and Clinician Guide | Troubleshooting Runbook |
| Primary care clinician | User and Clinician Guide | API and Data Reference, sections on time and provenance |
| Endocrinologist | User and Clinician Guide | API and Data Reference, glucose and threshold sections |
| Cardiologist | User and Clinician Guide | API and Data Reference, heart-rate and gap sections |
| Installer or operator | Installer Quick Start | Installation and Operations, Distribution and Installer |
| Security or privacy reviewer | Architecture, Security, and Privacy | Validation, Release, and Governance |
| Agent or integration developer | API and Data Reference | Architecture, Security, and Privacy |

## Claims and boundaries

- The dashboard reports observations. It does not determine why a value changed.
- A shared timeline does not establish correlation or causation.
- A missing or stale sample is a data-availability condition, not evidence of normal physiology.
- The configured 80 mg/dL value is a user-specific conversational context threshold. It is not an alarm threshold and cannot be changed by an agent.
- Live heart rate is displayed as a number only when the newest direct measurement is no more than ten seconds old, covering the five-second watch delivery cadence without flicker.
- Smoothed heart-rate drawing changes only the display curve. Stored values, tooltips, tables, exports, and APIs retain raw measurements and timestamps.
- Access control and encryption reduce risk but do not make a general compliance certification claim. Any clinical organization must perform its own legal, privacy, security, and workflow review before adopting the system.

## Sharing guidance

The User and Clinician Guide is suitable for sharing with a care team. The Architecture and API documents may be shared with technical reviewers. The Installation, Operations, and Troubleshooting documents should be limited to trusted operators because they describe system topology and recovery procedures.

Never add credentials, pairing files, service tokens, Libre account details, device secrets, private IP addresses, real health payloads, or unredacted screenshots to this documentation set.
