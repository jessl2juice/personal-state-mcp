# Fable Re-review: Samsung Health Data Adapter Revision 3

Date: 2026-09-26
Reviewer role: Fable

## Decision

Blocked pending revision 4.

## Findings

### P1-1: sleep-family reconciliation is incomplete

The manifest does not explicitly include every record derived from one Samsung sleep parent, and parent deletion does not clearly remove the summary, sessions, score, associated oxygen, and associated temperature.

Required resolution: define the manifest as every derived metric and record hash; make parent deletion an atomic empty manifest or association cascade; tombstone every removed member; test deletion, removed associated data, and interruption.

### P1-2: Samsung checkpoints are keyed to the wrong unit

Samsung change reads are scoped to provider data type, while one changed `SleepType` produces several Personal State metrics.

Required resolution: key checkpoints by Samsung provider type or a defined logical stream, make all normalized outputs from one provider change one commit unit, and define pagination/upload-split acknowledgement. Availability remains metric-specific.

### P1-3: the pairing file is an unmodeled plaintext secret boundary

The record identity key appears in the pairing file before Android Keystore storage, and rekey does not retire the old namespace.

Required resolution: include the pairing file in the threat model; define generation, expiry, import, storage, deletion, regeneration, and revocation; correct the Keystore claim; make rekey a transactional namespace migration.
