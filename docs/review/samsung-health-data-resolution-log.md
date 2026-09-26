# Samsung Health Data Review Resolution Log

Date: 2026-09-26
Reviewer role: Fable
Revised artifact: `docs/samsung-health-data-adapter-design.md`, revision 4

## Gate

Fable's initial review and revision-2 and revision-3 re-reviews were blocked. Revision 4 resolves the remaining sleep-family deletion, provider-type checkpoint, atomic source-change, and pairing-file boundary findings. Implementation remains paused until Fable approves revision 4.

| Finding | Resolution |
|---|---|
| P1-1 v1 cannot represent two adapters | Added a closed v2 envelope with adapter identity on the envelope, observations, deletions, and availability. Defined canonical v1 adapter mapping, dual-version acceptance, migration 3, persisted provenance, exports, backup, rollback, and v1-only operation. |
| P1-2 silent MCP expansion | Kept existing v1 shapes unchanged and split Samsung sessions, continuous oxygen, sleep summary, sleep score, skin temperature, Energy Score, rhythm notifications, apnea signs, floors, and active time into separate default-denied metrics. Default sleep access is adapter-restricted to Health Connect. Added current-state exclusion and enumerated tests. |
| P1-3 lossy Samsung mappings | Added property-level mappings for oxygen interval/series/min/max, multi-session sleep children and parent associations, sleep score, associated data, local-date Energy Score, offsets, strict enums, floors, and active time. |
| P1-4 undefined deduplication | Replaced destructive winner logic with independent source persistence. Defined adapter-scoped identity/deletion, narrow reversible display grouping, precedence, false-match policy, and categories never heuristically grouped. |
| P1-5 unsafe availability | Keyed availability by installation, adapter, and metric. Added `adapter_not_installed`, constrained partnership failure to positive authorization errors, and defined a separate merged dashboard view. |
| P1-6 incomplete sync lifecycle | Kept token semantics only for Health Connect. Defined Samsung change-time high-water marks, fixed run bounds, overlap, temporary pagination, post-ack advancement, clock rollback, same-time replay, delete handling, bounded backfill, sleep-manifest reconciliation, reinstall behavior, and adapter isolation. |
| P2-1 clinical labels exceed provider semantics | Removed vendor-reason fields, retained strict provider enums, renamed apnea to `Samsung Health Monitor detected sign`, distinguished recorded `not_detected` from absent data, and constrained rhythm status. |
| P2-2 prerequisites/freshness not normative | Pinned SDK 1.1.0, Samsung Health 6.30.2+, Android 10+, Java 17, public package/signature/scope approval, and a fixed source-aware freshness matrix with four independent ages. |

## Revision 2 Re-review Resolutions

| Finding | Resolution |
|---|---|
| Samsung uses change time, not tokens | Replaced Samsung token language with a fixed-bound, five-minute-overlap change-time workflow and temporary page tokens. Removed the false extended-history permission dependency. |
| Identity and reinstall contradiction | Added a server-owned record-identity HMAC key to v2 pairing, exact domain-separated inputs, phone storage, server validation boundary, stable reinstall behavior, upload-secret independence, and explicit rekey semantics. |
| Unstable sleep child identity | Replaced list indexes with a canonical session hash and added acknowledged parent-child manifests plus atomic server reconciliation for reorder, split, merge, edit, removal, and reinstall. |
| Samsung sleep widens MCP access | Added `sleep.samsung_session` as a separate default-denied metric and restricted default `sleep.session` exposure to Health Connect. Dashboard grouping cannot affect MCP results. |
| Non-deterministic availability and backup | Added active-installation selection, a 26-hour TTL, total state/tie precedence, and contributor display. Replaced the encryption claim with a concrete same-volume ACL and disk-encryption boundary, hash verification, restore steps, and user-controlled deletion. |

## Revision 3 Re-review Resolutions

| Finding | Resolution |
|---|---|
| Sleep-family orphan risk | Defined one atomic source change and complete manifest containing every derived metric/hash. Parent delete is an empty manifest and association cascade. Removed members are tombstoned; interrupted application is all-or-none. |
| Metric-keyed Samsung checkpoints | Keyed Samsung high-water marks to provider DataType. All normalized outputs from one provider change are one atomic commit unit; upload splitting is allowed only between units; checkpoints advance after every page is acknowledged. |
| Pairing-file secret boundary | Added an explicit 15-minute, one-time, revocable pairing lifecycle, signed confirmation, Keystore import, deletion status, regeneration, disclosure response, and public-artifact exclusions. Added identity namespace ids and transactional rekey activation/retirement. |

## Implementation authorization

After approval, the original user authorization permits implementation of the non-proprietary v2 server, migration, dashboard, fake adapter, fixtures, tests, and build isolation. It does not permit accepting Samsung legal terms for the user, redistributing the Samsung SDK contrary to its license, or claiming Samsung production authorization before it is granted.

## Revision 4 approval

Fable re-reviewed revision 4 and found no remaining P0, P1, or P2 design findings. The review approved the non-proprietary server, migration, dashboard, fake adapter, fixtures, tests, and build isolation. The concrete Samsung reader and Samsung-enabled public distribution remain gated on legitimate SDK access, licensing, device verification, and Samsung authorization.

## Licensed reader implementation review

Fable's first implementation review identified four P1 blockers. The reader was not committed while those findings were open.

| Finding | Resolution |
|---|---|
| Incremental sync missed old edits and deletes | Replaced measurement-time reconciliation with paginated Samsung changed-data reads after initial backfill, a five-minute overlap, explicit UPSERT and DELETE source changes, fixed run bounds, and post-ack provider checkpoints. |
| Sleep families omitted associated measurements | Added Samsung associated-data reads for blood oxygen and skin temperature and included those records in the parent sleep source change and exact replacement manifest. |
| Series and sleep data were silently truncated | Removed `.take(...)` clipping. Enforced explicit series and 500-member family limits; failures report interrupted and truncated availability and leave checkpoints unchanged. |
| Licensed minified reader lacked meaningful automation | Kept the full reflection-loaded class through R8, added fail-closed static invariants to public CI, and added a private-AAR verifier covering tests, release lint, minification, APK creation, and class retention. |

Fable re-reviewed the unchanged implementation after 47 Python tests, a clean public Android build, and the private Samsung-AAR release verification passed. The implementation review found no remaining P0 or P1 blocker and returned **APPROVED**. Owner-device validation remains required, and Samsung-enabled public distribution remains subject to the applicable Samsung authorization and license.
