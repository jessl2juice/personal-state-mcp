# Fable Re-review: Samsung Health Data Adapter Revision 2

Date: 2026-09-26
Reviewer role: Fable

## Decision

Blocked pending revision 3.

## Findings

### P1-1: Samsung synchronization uses change time, not durable change tokens

Samsung Health Data SDK reads changes over a change-time range and uses temporary pagination tokens. Revision 2 incorrectly inherited Health Connect token semantics and an extended-history permission.

Required resolution: use a persisted change-time high-water mark, fixed run upper bound, overlap window, paginated reads, idempotent replay, and post-ack advancement. Specify clock rollback, same-time changes, deletes, and interrupted pages. Treat 30-day backfill as Personal State policy.

### P1-2: provider identity and reinstall idempotence are contradictory

Revision 2 both requires provider ids and says they are hashed before upload, without defining the shared key or reinstall behavior.

Required resolution: define HMAC input, key owner, pairing path, domain separation, validation, reinstall behavior, and rotation.

### P1-3: sleep child identity is unstable

List-index identity changes when sessions reorder, split, merge, or change and can leave orphan rows.

Required resolution: derive child identity from canonical parent and session properties, persist a parent-child manifest, and delete children no longer present.

### P1-4: Samsung sleep still widens the existing agent metric

Samsung SDK sleep children still use default-allowed `sleep.session`.

Required resolution: use a separate default-denied Samsung session metric or adapter-level MCP projection and specify duplicate handling.

### P2-1: merged availability and backup are non-deterministic

Required resolution: define a total state order, TTL, and active-installation rule. Remove the unsupported application-level encryption claim or define a real encryption and recovery mechanism.
