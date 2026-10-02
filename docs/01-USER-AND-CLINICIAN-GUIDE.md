# Personal State User and Clinician Guide

Document version: 1.0  
Last verified: 2026-09-29
Intended readers: user, primary care, endocrinology, cardiology, and other authorized clinicians

## What Personal State does

Personal State brings Libre glucose, direct Galaxy heart rate, direct Fitbit Bluetooth heart rate, Samsung Health history, Google Fit archive records, and synchronized Fitbit/Google Health observations into one private review surface. The dashboard emphasizes what was measured, when it was measured, when it reached the system, how old it is, where it came from, and where coverage is incomplete.

It supports review and conversation. It does not diagnose a condition, determine causality, predict an emergency, provide treatment instructions, or replace the official Libre, Samsung, Fitbit, or Google applications.

## The main view

The main view is organized around a shared recorded-time axis:

- Glucose is shown in mg/dL with its own labeled lane and scale.
- Heart rate is shown in beats per minute with its own labeled lane and scale.
- Day is the default view. Week, Month, and Year are available for longer patterns.
- The two streams remain separate and visually identifiable while sharing the same time axis.
- The 80 mg/dL line is a configured decision-support context threshold, not an alarm.
- Coverage and gaps remain visible. The display does not compress unrelated dates into apparent alignment.

## Current values and freshness

Every current value should be read together with its timestamp and age.

### Glucose

The glucose card identifies the measurement time, receipt time, age, trend when supplied upstream, and source. The service classifies glucose as:

- `fresh`: within 10 minutes under the current configuration.
- `recent`: older than 10 minutes but within 30 minutes.
- `stale`: older than 30 minutes, missing required timing, or implausibly future-dated.
- `unavailable`: no stored reading exists.

Only fresh or recent glucose can produce below, near, or above threshold context. Stale data produces an unknown context state.

### Heart rate

The live heart-rate position shows a number only when the newest direct Galaxy Watch or Fitbit Bluetooth measurement is no more than ten seconds old. At eleven seconds it changes to `No live data`; an older reading remains available in history but never occupies the live-vital position.

This rule prevents a technically valid but old value from looking current.

## Reading the timeline

The timeline is descriptive. It can answer questions such as:

- Were glucose and heart-rate measurements recorded during the same period?
- What range was observed in each stream?
- Are apparent changes supported by dense measurements or separated by gaps?
- Did a displayed value come from Libre, the watch, Samsung Health, Google Fit history, Fitbit, or another Google Health origin?

It cannot answer why a change happened. A visible sequence does not prove one signal caused the other.

Heart-rate display smoothing uses a centered, weighted seven-sample curve inside continuous segments. The line may bridge gaps up to one minute for readability. A gap longer than one minute creates a visible break. The system does not invent intermediate measurements, and all exact-value surfaces continue to use raw data.

## Provenance and time fields

Personal State separates several times because they mean different things:

- `measured_at`: when the sensor or source says the observation occurred.
- `received_at`: when glucose was received from the Libre-compatible path.
- `observed_by_companion_at`: when the Android companion read a Health Connect record.
- `ingested_at_server`: when the Personal State service accepted the watch record.
- `stored_at`: when a normalized glucose reading was persisted locally.
- `generated_at`: when the dashboard or API response was created.

For watch and Samsung data, the interval from measurement to companion observation includes unknown vendor synchronization and phone collection delay. It is not a verified device-delivery time.

## Gaps and incomplete data

Missing data is presented as missing data. It is not converted to zero and is not interpreted as a normal value.

Common causes include:

- Watch removed from the wrist or poor sensor contact.
- Watch monitoring paused, stopped, or temporarily owned by another workout application.
- Bluetooth or Wear OS Data Layer interruption.
- Phone process, network, or authenticated upload interruption.
- Libre follower data delay or upstream service changes.
- Health Connect permission or source-availability limitations.
- Fitbit phone-app or Google Health synchronization delay.

The dashboard can know when the companion last read Health Connect, when the server last accepted an upload, when a Google Health record reached Personal State, and when a direct Fitbit Bluetooth heart-rate packet was received. It generally cannot prove when the watch last synchronized to Samsung Health or when the Fitbit application will next synchronize. Fitbit/Google Health cloud and Health Connect values are never labeled as direct live measurements.

## Clinician workflows

### Primary care

User story: As a primary care clinician, I need a quick factual overview of current state, selected-period ranges, completeness, and source timing so I can identify topics for discussion without starting with raw records.

Suggested review:

1. Confirm the selected date range and generated time.
2. Check current-value freshness before reading the values.
3. Review coverage and gaps for both streams.
4. Review selected-period count, average, median, range, variability, and first/last capture.
5. Use the detailed table or CSV only when source-level investigation is needed.

### Endocrinology

User story: As an endocrinologist, I need glucose statistics and threshold context to remain primary while heart rate is available as time-aligned context without a causal claim.

Suggested review:

1. Confirm Libre provenance and glucose measurement density.
2. Review count, mean, median, minimum, maximum, standard deviation, below-threshold count, and gaps.
3. Treat the 80 mg/dL line as the user's configured context threshold, not a clinical alert definition.
4. Use heart rate only as contemporaneous context when the measurement windows overlap.
5. Confirm suspected events in the official Libre record when clinical decisions depend on them.

### Cardiology

User story: As a cardiologist, I need heart-rate history, count, range, average, capture dates, and alignment with glucose on one time axis without decoding a misleading overlay.

Suggested review:

1. Confirm heart-rate source, timestamps, sample count, and coverage.
2. Distinguish live direct Fitbit or Galaxy data from synchronized Fitbit, Google Health, Health Connect, and Samsung Health historical records.
3. Inspect visible gaps before interpreting line shape.
4. Use raw table values or CSV for exact measurements; do not read exact values from the smoothed display curve.
5. Confirm clinically significant events with an appropriate medical record or diagnostic device.

## Preparing a clinical handoff

Before a visit:

1. Choose the relevant Day, Week, Month, or Year view.
2. Confirm both data streams have the expected coverage.
3. Use the clinical print view for a readable summary.
4. Export CSV when the clinician needs exact observations for analysis.
5. Note sensor changes, watch-off periods, travel, illness, exercise, medication changes, or other context separately. Personal State does not infer these events.

The printed review should include the generation time, selected range, current freshness, timeline, coverage, descriptive facts, and safety boundary.

## Agent use

An authorized agent may call Personal State when the user seems unusually confused, inconsistent, indecisive, or otherwise off. The agent must:

- State measurement time and freshness when mentioning a value.
- Describe observations without diagnosing a cause.
- Treat stale, missing, or unavailable data as unknown.
- Avoid alarms, treatment advice, and automated action.
- Direct the user to the official device application or appropriate care when authoritative or urgent information is needed.

Agents cannot change thresholds, permissions, credentials, retention, or safety instructions.

## Safety statement

Use the Libre application and sensor for glucose alerts. Use Samsung Health, Samsung Health Monitor, Fitbit, Google Health, and appropriate medical devices for their supported features and notices. Seek professional or emergency care based on symptoms and established care guidance, not on Personal State alone.
