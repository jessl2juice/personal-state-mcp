# Personal State MCP Design

Status: revised after Fable review

## Research Baseline

This design was checked against current public sources on 2026-09-24.

- Abbott describes the supported sharing path as FreeStyle Libre app to LibreView cloud to LibreLinkUp followers. Libre 3 Plus is supported through the Libre app/LibreView/LibreLinkUp path, and data sharing requires internet connectivity on the user's device. Sources: [LibreLinkUp FAQ](https://www.freestyle.abbott/en-ca/support/how-does-the-librelinkup-app-work.html), [Libre 3 Plus compatibility FAQ](https://www.support.freestyle.abbott/hc/en-us/articles/27194595253393-Do-LibreLinkUp-users-need-to-update-their-LibreLinkUp-app-to-continue-receiving-readings-from-their-connections-when-they-switch-from-the-FreeStyle-Libre-3-to-FreeStyle-Libre-3-Plus-sensor), [Libre app FAQ](https://www.support.freestyle.abbott/hc/en-us/articles/43263373536785-Do-FreeStyle-Libre-3-Plus-sensors-work-with-the-Libre-app).
- Abbott's LibreLinkUp terms describe LibreLinkUp/LibreView as information services, not diagnostic tools, and say users should not treat them as a substitute for professional advice. Source: [LibreLinkUp EULA](https://files.libreview.io/files/documents/en-US/LLU_EULA_2025-05-27.html).
- There is no official consumer LibreLinkUp API contract for this use. Current community integrations use an unofficial LibreLinkUp/LibreView-compatible API that can break without notice.
- `lozit/mcp-freestyle` is current and useful as a reference, not a dependency. It is a Node MCP server that reads Libre data through the unofficial LibreLinkUp/LibreView API, stores credentials in the OS keychain, uses `LIBRELINKUP_VERSION=4.16.0`, reports the observed approximately 12-hour upstream history window, and explicitly avoids safety/treatment claims. Source: [lozit/mcp-freestyle](https://github.com/lozit/mcp-freestyle).
- `lozit/mcp-freestyle` architecture notes say the current unofficial contract uses `/llu/auth/login`, `/llu/connections`, and `/llu/connections/{patientId}/graph`, with regional host discovery and `product: llu.android` plus version headers. It also notes current graph history is capped around 12 hours and may lag the current reading. Source: [lozit architecture notes](https://raw.githubusercontent.com/lozit/mcp-freestyle/main/docs/ARCHITECTURE.md).
- Python remains a suitable stack. The current MCP Python SDK v2 exposes tools with `MCPServer` and `@mcp.tool()` and supports stdio by default. Source: [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/).
- Samsung/Galaxy Watch future path should support two adapters: Samsung Health Data SDK for local Android app access, and Health Connect as a normalized Android data layer. Samsung documents that Galaxy Watch data transfers to Samsung Health on the phone and can synchronize to Health Connect with user-granted permissions. Sources: [Samsung Health Data SDK](https://developer.samsung.com/health/data/guide/introduction.html), [Samsung Health Connect FAQ](https://developer.samsung.com/health/health-connect-faq.html).
- Fitbit future path should avoid new reliance on legacy Fitbit Web API. Google says the Fitbit Web API is now evolving into Google Health API and that the legacy Fitbit Web API is being turned down in September 2026. Source: [Google Health API overview](https://developers.google.com/health/about).

## Goals

1. Give the user's agents selective, read-only access to physiological context when the user appears unusually confused, inconsistent, indecisive, or "off".
2. Start with FreeStyle Libre 3 Plus data through the current viable LibreLinkUp/LibreView-compatible path.
3. Preserve a stable MCP contract while vendor-specific adapters change behind it.
4. Return the most recent glucose, trend, measurement timestamp, received timestamp, data age, freshness, recent history, provenance, and a configured user-specific context threshold of 80 mg/dL.
5. Persist long-term time-series history locally so agents are not limited to Abbott's short upstream history window.
6. Keep every response explicit that this is informational decision support, not an alarm, diagnosis, causality engine, or treatment system.
7. Keep secrets out of files by using the OS keychain or secret store where practical.
8. Make the design testable without real credentials or real glucose data.

## Non-Goals

- No alarms, urgent notifications, safety monitoring, insulin dosing, food recommendations, treatment automation, or clinical diagnosis.
- No claim that low glucose caused a behavior change.
- No replacement for the Libre app, sensor alerts, glucose meter, physician guidance, or emergency care.
- No agent-write tools for thresholds, safety instructions, credentials, or vendor account settings.
- No hosted service or multi-user cloud product in v1.
- No attempt to bypass Abbott, Samsung, Fitbit/Google, Android, or OS permission models.

## System Overview

```
FreeStyle Libre 3 Plus sensor
  -> Libre app on phone
  -> LibreView cloud
  -> LibreLinkUp follower-compatible adapter
  -> collector
  -> local SQLite time-series store
  -> stable domain model
  -> MCP tools for agents
```

The local MCP process is the only component an agent can call. It has read-only tool endpoints. A separate collector path pulls vendor data and upserts normalized readings into local storage. MCP tools may trigger a conservative refresh, but they still answer from the normalized store and include freshness and provenance semantics.

## Architecture

### Packages

- `personal_state_mcp.models`: normalized dataclasses and response envelopes.
- `personal_state_mcp.config`: local configuration loading, threshold policy, and path selection.
- `personal_state_mcp.freshness`: age and staleness classification.
- `personal_state_mcp.storage`: SQLite schema and read/write repository.
- `personal_state_mcp.adapters.base`: adapter protocol and collection result.
- `personal_state_mcp.adapters.libre_linkup`: LibreLinkUp-compatible client and mapper.
- `personal_state_mcp.collector`: adapter orchestration, polling guardrails, persistence.
- `personal_state_mcp.service`: stable application service used by MCP tools and tests.
- `personal_state_mcp.mcp_server`: MCP tool surface.
- `personal_state_mcp.cli`: local setup, one-shot collection, and debug status commands.

### Boundaries

- Vendor adapters emit normalized domain records only.
- The collector handles polling intervals, persistence, and errors.
- MCP tools never expose raw credentials, never mutate config, and never call treatment actions.
- Service responses are serializable dictionaries so the MCP layer stays thin.

## Adapter Contract

Every adapter implements:

- `name`: stable adapter id, such as `libre_linkup`.
- `collect(now) -> CollectionResult`: fetches current/recent records and returns normalized readings plus upstream metadata.
- `supports(metric) -> bool`: declares whether the adapter can provide a metric family.

Normalized adapter output:

- `GlucoseReading`
  - `value_mg_dl`
  - `measured_at`
  - `received_at`
  - `trend`
  - `trend_rate_mg_dl_per_min`
  - `sample_type`: current, history, scan, event, unknown
  - `source`: vendor, adapter id, account/connection id hash, sensor id hash if available
  - `raw`: optional redacted payload fragment for debugging

Adapters must not:

- Store credentials in project files.
- Make treatment suggestions.
- Hide upstream errors.
- Guess missing trend mappings as fact.
- Interpolate across data gaps.

## LibreLinkUp Adapter

The first adapter signs in as a LibreLinkUp follower account, not the user's primary sensor account. The expected practical setup is:

1. The Libre 3 Plus sensor is paired to the official Libre app.
2. The Libre app uploads to LibreView.
3. The user invites a separate LibreLinkUp follower account.
4. This MCP stores the follower password in the OS keychain and authenticates as that follower.

The adapter follows current community observations:

- Start at `https://api.libreview.io`.
- Login with `/llu/auth/login`.
- Follow regional redirects returned by the API.
- Reject HTTP redirects for credential-bearing requests; region changes are accepted only from authenticated JSON responses.
- Accept only `https://api.libreview.io` or `https://api-<region>.libreview.io` hosts, where `<region>` is a conservative alphanumeric/hyphen region token returned by LibreView.
- Never forward credentials or bearer tokens to arbitrary hosts.
- Record the selected regional host in collector metadata without recording credentials.
- Use `/llu/connections` for the current reading and connection metadata.
- Use `/llu/connections/{patientId}/graph` for recent graph history.
- Preserve both measurement time and local receipt time.
- Treat the upstream graph window as short and bounded; persist locally to build long-term history.
- Poll conservatively, defaulting to no faster than 60 seconds.

`lozit/mcp-freestyle` is not imported because it is Node-based, has a narrower tool surface, and intentionally omits persistence. It remains a valuable reference for the observed upstream contract, regional redirect behavior, keychain posture, and staleness humility.

## Data Model

### Tables

`glucose_readings`

- `id`: deterministic hash of adapter, source connection, measured time, value, and sample type.
- `adapter`: `libre_linkup`, later `samsung_health`, `google_health`.
- `source_patient_hash`: one-way hash of patient/connection id.
- `value_mg_dl`: integer.
- `trend`: normalized string when known.
- `trend_raw`: raw vendor trend marker.
- `sample_type`: current, history, scan, event, unknown.
- `measured_at_utc`: ISO-8601.
- `received_at_utc`: ISO-8601.
- `stored_at_utc`: ISO-8601.
- `provenance_json`: redacted source details.
- `raw_json`: optional redacted raw sample.

`collector_runs`

- `id`
- `adapter`
- `started_at_utc`
- `finished_at_utc`
- `status`
- `readings_seen`
- `readings_inserted`
- `error_code`
- `error_message`

`access_audit`

- `id`
- `accessed_at_utc`
- `host_id_hash`
- `tool_name`
- `decision`: allowed, denied, rate_limited, error.
- `reason`

`config_audit`

- `id`
- `changed_at_utc`
- `actor`: local_cli, manual_file_edit, migration.
- `field`
- `old_value_hash`
- `new_value_hash`

The implementation starts with `glucose_readings`, `collector_runs`, and `access_audit`. `config_audit` is part of the stable design for the first real settings UI/CLI that mutates config.

## Collector

The collector has two modes:

- `collect-once`: manually fetch and persist current/recent data.
- `collect-loop`: long-running local process with a minimum poll interval.

MCP tool calls do not refresh upstream data by default. Opportunistic refresh must be explicitly enabled in local config, and even then is subject to the adapter minimum polling interval. Refresh failures do not erase existing history. Responses surface the failure and the freshest stored reading.

Collector behavior:

- Upsert readings idempotently.
- Record every collector run.
- Preserve gaps as missing data.
- Rate-limit calls per adapter.
- Redact identifiers before writing raw diagnostic payloads.
- Keep logs on stderr for MCP safety.

## Persistence

SQLite is the v1 store because it is local, durable, portable, easy to back up, and does not require a server. Default location:

- Windows: `%LOCALAPPDATA%\PersonalStateMCP\state.db`
- macOS/Linux: `~/.local/share/personal-state-mcp/state.db`

Tests and local development can override this with `PERSONAL_STATE_MCP_DB`.

Retention:

- Default: keep all local readings indefinitely so the system can exceed upstream history windows.
- Optional `retention_days` prunes older local readings.
- V1 includes local export and delete commands so long-term history is user-controllable from the start.

At-rest encryption:

- Not built into v1 because transparent encrypted SQLite creates portability and recovery complexity.
- The project recommends storing the database in the user's encrypted OS profile/disk.
- Future hardening can add SQLCipher or platform storage if the user wants stronger local protection.

## MCP API

All tools are read-only. None can change thresholds, credentials, polling frequency, or safety instructions.

Every MCP response uses this envelope:

```json
{
  "ok": true,
  "tool": "health.glucose",
  "generated_at": "2026-09-24T20:30:00Z",
  "data": {},
  "freshness": {
    "status": "fresh",
    "reason": "latest measurement is within the fresh window",
    "measurement_age_seconds": 240,
    "received_age_seconds": 180,
    "max_age_seconds": 600
  },
  "provenance": {
    "adapter": "libre_linkup",
    "vendor": "abbott_libreview",
    "source": "libre_linkup_follower",
    "measurement_timestamp_source": "unix_timestamp",
    "received_timestamp_source": "collector_clock"
  },
  "safety": {
    "use": "informational_context_only",
    "not_for": ["alarms", "diagnosis", "treatment_automation"],
    "message": "Use the official Libre app/sensor for alerts and health decisions."
  },
  "errors": []
}
```

Error responses preserve the same envelope and set `ok: false`. `errors[]` entries include `code`, `message`, and `retryable`. Health data fields are `null` when unavailable rather than guessed.

### `health.current_state()`

Returns a compact summary for "the user seems off" checks:

- current glucose summary
- freshness status
- configured threshold
- recent trend/history summary
- explicit limitations
- recommended agent behavior

### `health.glucose()`

Returns the freshest known glucose reading:

- `value_mg_dl`
- `trend`
- `measured_at`
- `received_at`
- `stored_at`
- `age_seconds`
- `freshness`
- `provenance`
- `threshold_context`
- `safety_notice`

### `health.glucose_recent(hours=3, limit=96)`

Returns recent local history:

- readings
- requested range
- returned range
- gaps
- source coverage
- freshness for the newest reading
- indication when local history extends beyond upstream window

### `health.context()`

Returns agent-facing decision-support context:

- "The user-configured context threshold is 80 mg/dL."
- Whether the latest stored reading is below, near, or above that threshold.
- A prohibition on causal claims and treatment instructions.
- Suggested wording: "Your glucose context may be relevant; please check your official Libre app/sensor if you feel unwell."
- Data provenance and freshness.

Threshold states:

- `below`: latest fresh or recent reading is `< 80 mg/dL`.
- `near`: latest fresh or recent reading is `>= 80 and < 90 mg/dL`.
- `above`: latest fresh or recent reading is `>= 90 mg/dL`.
- `unknown_stale`: a reading exists but freshness is `stale`.
- `unavailable`: no reading exists.

Agents must not describe threshold state as below, near, or above when the latest reading is stale. In that case they may only say the latest stored data is too old to interpret against the threshold.

## Freshness and Staleness Semantics

Every response includes:

- `generated_at`
- `measured_at`
- `received_at`
- `stored_at`
- `measurement_age_seconds`
- `received_age_seconds`
- `freshness.status`
- `freshness.reason`
- `freshness.max_age_seconds`
- `provenance.adapter`
- `provenance.vendor`

Default glucose freshness bands:

- `fresh`: measured no more than 10 minutes ago and received no more than 10 minutes ago.
- `recent`: measured no more than 30 minutes ago.
- `stale`: measured more than 30 minutes ago or receipt time is missing/old.
- `unavailable`: no reading exists or parsing failed.

Freshness status is about data age, not user safety.

Timestamp normalization rules:

1. Prefer explicit Unix/epoch timestamps from the vendor payload.
2. Next prefer ISO-8601 timestamps that include an offset or `Z`.
3. If the vendor provides local timestamps without timezone, interpret them using the configured source timezone. If no source timezone is configured, use the local machine timezone and mark `measurement_timestamp_source` accordingly.
4. `received_at` is the local collector clock when a payload is successfully received. It is never substituted for `measured_at`.
5. Future measurements more than five minutes ahead of local clock are flagged stale with reason `future_measurement_clock_skew`.
6. Missing or unparsable measurement timestamps make the reading unavailable for current-state decisions, though a redacted parse error is recorded in the collector run.

## Local Access Policy

The MCP server is local, but access is still mediated:

- Each MCP host should be configured with a local `host_id`.
- `allowed_hosts` controls which host ids may read data.
- Every MCP read attempt is written to `access_audit` with allowed/denied/rate-limited status.
- A per-host read limit defaults to 30 health-tool calls per minute.
- Opportunistic refresh is disabled unless `opportunistic_refresh_enabled` is set.
- No MCP tool can reveal secrets, update credentials, update thresholds, disable safety copy, or change policy.

If a host cannot provide a unique identity, the installer may use a local configured id such as `default-local`; stronger per-host isolation can be added later by host-specific MCP config entries.

## Agent Behavior Contract

Agents may call this MCP only when physiological context could help interpret interaction quality, for example unusual confusion, repeated contradictions, indecision, or the user explicitly asks about state.

Agents must:

- Treat data as context, not diagnosis.
- Avoid claiming glucose caused behavior.
- Mention freshness and measurement time when using the data.
- Encourage checking the official Libre app/sensor for any concern.
- Ask the user before using the context in a sensitive conversation summary.
- Avoid storing glucose values in unrelated task artifacts.

Agents must not:

- Tell the user to consume sugar, dose insulin, drive, exercise, sleep, or change treatment.
- Use MCP responses as alarms.
- Keep polling repeatedly during ordinary conversation.
- Change thresholds or safety instructions.

## Failure Modes

- Upstream API changes: adapter returns `source_unavailable` and includes last successful collection metadata.
- Libre app not uploading: readings become stale; MCP still returns last known data with staleness warning.
- LibreLinkUp sharing revoked: adapter returns authorization failure and setup hint.
- Credentials missing: adapter is unavailable and setup instructions are returned.
- Region redirect changes: adapter follows redirect when provided; no fixed allowlist.
- Rate limiting: collector backs off and records run failure.
- Clock skew: freshness includes both measured age and received age; impossible timestamps are flagged.
- Duplicate readings: deterministic ids make upserts idempotent.
- Database locked: MCP returns service unavailable with no data mutation.
- Raw payload shape change: adapter preserves sanitized parse error and fails closed.

## Privacy and Security

- Store passwords/tokens in OS keychain where practical.
- Allow environment variable override only for local development/CI, and mark it as less preferred.
- Never store secrets in SQLite or config files.
- Hash account, connection, and sensor identifiers before persistence.
- Avoid raw glucose examples from the real user in tests, fixtures, screenshots, or docs.
- Default local-only stdio MCP transport.
- No hosted endpoint in v1.
- Read-only MCP tools only.
- No settings mutation over MCP.
- Minimal logging, no raw credentials, no full raw medical payloads.
- Explicit separation between safety alerts and informational agent context.

## Threat Model

### Assets

- LibreLinkUp follower credentials.
- Glucose time-series values and timestamps.
- Derived context such as below-threshold status.
- Local database.
- MCP tool outputs in agent transcripts.

### Adversaries

- A malicious prompt trying to make the agent reveal or misuse health data.
- Malware or another local user reading files.
- A compromised MCP host invoking tools too often.
- Upstream service/API instability.
- Accidental inclusion of health data in unrelated work.

### Mitigations

- No secret-returning tools.
- No write tools for config or thresholds.
- OS keychain for credentials.
- Local database outside project source by default.
- Tool descriptions and responses include use constraints.
- Staleness and provenance always included.
- Conservative polling and backoff.
- Tests use synthetic data.

## Observability

- `collector_runs` table records collection health without exposing credentials.
- CLI `status` reports last run, latest reading age, configured adapters, and database path.
- Logs go to stderr to avoid corrupting MCP stdio.
- Future: optional OpenTelemetry spans for local debugging, disabled by default.

## Deployment

Local stdio MCP server:

1. Install package in a local virtual environment.
2. Configure LibreLinkUp follower email and store password in the OS keychain.
3. Run `personal-state collect-once` to verify.
4. Add MCP server command to the desired MCP host.
5. Optionally run `personal-state collect-loop` as a user-level background task.

The MCP server can also collect opportunistically on tool calls, but a background collector is better for long-term history continuity.

## Test Plan

Unit tests:

- Freshness bands.
- Threshold context at below, near, above, unavailable.
- SQLite upsert and ordering.
- Gap detection.
- Idempotent collection.
- Adapter mapping from synthetic LibreLinkUp payloads.
- Redaction and hash behavior.

Contract tests:

- `health.glucose()` always includes provenance/freshness.
- `health.glucose_recent()` returns requested and actual range.
- `health.context()` never emits treatment instructions.
- MCP tools are read-only.

Integration tests:

- Fake adapter collector run into SQLite.
- Optional real Libre smoke test behind explicit environment flag and never in CI by default.

Security tests:

- No secret values in persisted DB.
- No secret values in structured tool output.
- MCP has no config-write tool.

## Samsung and Galaxy Watch Future Path

Future adapter choices:

1. Android companion collector using Samsung Health Data SDK for Samsung Health data that may include Galaxy Watch-derived metrics.
2. Health Connect collector as a normalized Android source for supported data types.

This MCP should keep those as source adapters, not special MCP tools. New metrics should normalize into the same response envelope with source, measured time, received time, stored time, freshness, and data gaps.

Likely future metrics:

- heart rate
- resting heart rate
- sleep summary
- activity/steps
- SpO2 where supported
- stress/HRV when legally and technically available

Constraints:

- Android permission grant must be explicit.
- Watch-to-phone sync timing is not guaranteed.
- Metrics are wellness context, not diagnosis.

## Fitbit / Google Health Future Path

Because Google states that the legacy Fitbit Web API is being turned down in September 2026, the future adapter should target Google Health API for Fitbit and Pixel Watch data rather than building new functionality on the legacy Fitbit Web API.

Likely design:

- Local OAuth consent flow.
- Read-only Google Health scopes.
- Token storage in OS keychain.
- Normalized metric adapters for heart rate, sleep, HRV, activity, and possibly glucose if available and permitted.
- Same freshness/provenance envelope as Libre.

The legacy Fitbit Web API may be useful only as a migration reference or for user-owned local testing if still available.

## Open Questions

- Whether the user wants the background collector installed as a Windows scheduled task or kept as a manual command.
- Whether to store redacted raw payload fragments by default or only under a debug flag.
- Whether to encrypt SQLite at rest after v1.
- Whether future agent hosts should have per-host allowlists for health tools.
