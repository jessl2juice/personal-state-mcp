# Personal State Architecture, Security, and Privacy

Document version: 1.0  
Last verified: 2026-09-26  
Intended readers: engineering, security, privacy, and technical clinical reviewers

## System objective

Personal State gives authorized users and agents selective, read-only access to physiological context with explicit freshness and provenance. It preserves history beyond upstream display windows while keeping vendor-specific collection behind normalized contracts.

The design favors honest uncertainty over inferred completeness. It never converts absence into a physiological conclusion and never automates treatment.

## Data flows

### Glucose

```text
Libre 3 Plus sensor
  -> official Libre application and Abbott services
  -> dedicated LibreLinkUp follower account
  -> Personal State Libre adapter
  -> normalized SQLite history
  -> dashboard, export, and authorized MCP tools
```

The official Libre application remains responsible for device alerts. The Personal State adapter uses a currently viable unofficial LibreLinkUp/LibreView-compatible path and can be affected by upstream changes.

### Historical Samsung Health data

```text
Galaxy Watch and/or phone
  -> Samsung Health
  -> Android Health Connect
  -> Personal State phone companion
  -> authenticated private ingest endpoint
  -> normalized SQLite history
  -> dashboard, export, and authorized MCP tools
```

A Samsung Health source package does not by itself prove that a record came from the watch. Watch attribution is asserted only when device metadata provides positive evidence.

### Live heart rate

```text
Galaxy Watch Health Services
  -> Personal State watch foreground service
  -> Wear OS Data Layer message plus urgent Data Item
  -> matching Personal State phone application
  -> signed private ingest request
  -> SQLite history and compact live endpoint
  -> dashboard
```

The direct path requests heart rate only, disables GPS, uses five-second screen-off batching, relays at most once every two seconds, and automatically recovers its own stalled exercise stream after 45 seconds.

## Major components

### Adapter boundary

Vendor adapters emit normalized observations rather than exposing vendor response shapes. The current adapters are Libre-compatible glucose and Android Health Connect plus direct watch heart rate. Future Fitbit or Samsung-specific adapters must preserve the same time, provenance, freshness, and safety contracts.

### Collector

The collector enforces a minimum polling interval, records collection runs, and writes idempotent normalized observations. MCP reads do not trigger upstream network calls unless opportunistic refresh is explicitly enabled.

### Storage

SQLite stores normalized glucose, normalized health observations, source metadata, collection runs, access audit records, watch availability, replay state, and sync metadata. Raw vendor payloads and credentials are not stored.

### Dashboard

The dashboard binds only to loopback and serves the protected UI through Cloudflare Tunnel. It rejects unapproved Host headers, sends `Cache-Control: no-store`, uses restrictive browser security headers, and requires a per-process token for state-changing local requests.

### MCP server

The MCP server uses stdio and exposes read-only tools. A host allowlist, rate limit, audit log, exposure allowlist, and fixed safety wording constrain access. Agents cannot write health records or modify configuration.

## Trust boundaries

1. Vendor boundary: Libre and Samsung services are external sources with independent behavior and availability.
2. Device boundary: the watch and phone are separate devices connected by Wear OS Data Layer.
3. Ingest boundary: the phone authenticates to a dedicated ingest hostname that cannot serve the dashboard.
4. Dashboard boundary: interactive access is protected by a separate Cloudflare Access application.
5. Local host boundary: the Python service and SQLite database run under the signed-in Windows user.
6. Agent boundary: MCP hosts receive only allowlisted, read-only context and cannot access secrets.

## Authentication and authorization

- Libre uses a dedicated follower identity. Its password is held in the OS credential store.
- Dashboard access uses Cloudflare Access interactive authentication.
- Phone ingest uses a separate Access service identity plus device-level HMAC authentication.
- Ingest requests carry device id, timestamp, nonce, batch id, and signature.
- Nonces and batch ids provide replay and idempotency controls.
- MCP access uses host id allowlisting and per-host rate limiting.
- Watch-to-phone Data Layer delivery requires matching package name and signing certificate.

Dashboard and ingest credentials are intentionally not interchangeable.

## Data minimization

- Collection is read-only.
- No Health Connect write, route, or location permission is requested.
- Live watch collection requests heart rate only and disables GPS.
- Raw ECG, raw PPG, accelerometer streams, and exercise routes are excluded.
- Stress is not inferred from other signals.
- Agent exposure defaults to a narrower set than dashboard collection.
- Logs contain operational status, counts, and keyed identifiers, not measurement values or request bodies.

## Freshness and non-deception controls

- Glucose freshness is based on measurement and receipt times.
- Live heart rate is numeric only within 60 seconds of measurement.
- Old heart-rate values remain historical and cannot appear as current.
- Dashboard curves break after a one-minute heart-rate gap.
- Smoothed drawing never changes persisted or exported values.
- Stale glucose cannot trigger threshold classification.
- Every response includes a safety boundary and relevant provenance.

These controls are designed to prevent technically available but old data from looking current.

## Threat model

### Protected assets

- Physiological observations and long-term history.
- Libre account linkage and follower credentials.
- Device identity and pairing material.
- Cloudflare service and tunnel credentials.
- Threshold and agent-exposure policy.

### Principal threats and controls

| Threat | Controls |
| --- | --- |
| Credential theft | OS credential store, Android Keystore, DPAPI, no repository secrets, rotation and revocation procedures |
| Public endpoint discovery | Cloudflare Access, separate applications and audiences, host and path restrictions, loopback origin |
| Forged or replayed ingest | Per-device HMAC, timestamp skew limit, nonce store, batch idempotency, strict schema |
| Cross-surface privilege | Dashboard and ingest host isolation; ingest host serves health check and ingest only |
| Payload abuse | 1 MiB body limit, strict JSON parser, closed metric and unit allowlists, depth and count limits, transactional insert |
| Excess collection | Read-only permissions, user-selected categories, no routes or raw sensor streams |
| Misleading current state | 60-second live-heart cutoff, explicit ages, visible gaps, stale gating |
| Local database disclosure | Current-user ACLs, expected full-disk encryption, protected backups, no raw credentials |
| Agent overreach | Read-only MCP annotations, host allowlist, metric exposure allowlist, audit, no configuration tools |

## Privacy posture

Personal State is a private personal system. It does not upload health data to advertising or third-party analytics services. Data leaves the devices only for the private authenticated Personal State ingest path and authorized exports.

This architecture does not by itself establish HIPAA, GDPR, state-law, medical-device, or organizational compliance. Any organization receiving or operating the system must determine its own legal basis, contracts, retention, audit, breach response, and access policy.

## Residual risks

- A compromised or unlocked phone can access data and secrets available to the running application despite Keystore protection.
- A compromised Windows account may access the local database and active service session.
- The unofficial Libre-compatible interface may break or change without notice.
- Device and cloud timestamps may be delayed or wrong.
- Samsung Health does not expose every Galaxy Watch metric through Health Connect.
- Bluetooth, network, operating-system background policies, sensor contact, and battery conditions can create gaps.
- A care team could overinterpret consumer-sensor observations if source and completeness caveats are ignored.

## Incident priorities

1. Protect the user and defer to official device applications for safety-critical information.
2. Stop public or unauthorized access without destroying evidence.
3. Revoke affected credentials.
4. Preserve sanitized operational timestamps and error codes.
5. Determine which records, identities, and time windows may be affected.
6. Restore service only after authentication, integrity, and freshness behavior are verified.
7. Record the incident and corrective action without copying health payloads into general logs or tickets.
