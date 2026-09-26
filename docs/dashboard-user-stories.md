# Personal State Dashboard User Stories

## Product boundary

The dashboard presents recorded physiological data, timing, provenance, coverage, and descriptive statistics for personal and clinician review. It is not an alarm, diagnosis, causal inference, or treatment system. Libre and Samsung applications remain authoritative for their device alerts and official notices.

## Primary user

### Story

As the person whose data is shown, I need to identify glucose and heart rate immediately, see how current each value is, and know whether the two data streams overlap so that I can understand what was recorded without mistaking unrelated periods for a physiological relationship.

### Acceptance criteria

- Current glucose and heart rate are separate, equally prominent values with units, measurement time, upload or receipt time, and age state.
- The live heart-rate card shows a numeric value only when the sample is no more than 60 seconds old. At 61 seconds it switches to `No live data`; any last known value is labeled `Historical only`.
- The shared timeline uses separate labeled lanes and scales for glucose and heart rate.
- Glucose and heart rate retain consistent, high-contrast colors everywhere.
- The selected range and the coverage window for each stream are visible.
- Day is the default view, with Week, Month, and Year available as stable display options.
- Both sensors are always presented as one person's data on the same recorded-time axis.
- If a recent companion upload does not contain current heart-rate samples, the dashboard calls it a heart-rate sync failure.
- Hover details show both values only when they are within ten minutes of the selected point; otherwise the missing nearby measurement is explicit.
- Missing data and stale data are never presented as normal physiology.

## Primary care clinician

### Story

As a primary care clinician, I need a fast, factual overview of current state, selected-period ranges, completeness, and source timing so that I can decide what deserves discussion without reading raw device tables first.

### Acceptance criteria

- A selected-period facts panel summarizes glucose count, average, median, range, below-threshold observations, variability, heart-rate count, average, range, first and last capture, and heart-rate sync status.
- Data gaps and coverage windows are visible before detailed history.
- The page includes provenance and device-authority information.
- A clinical review button produces a clean print view containing the current state, timeline, coverage, and selected-period facts.

## Endocrinologist

### Story

As an endocrinologist, I need glucose statistics and threshold context to remain primary while heart rate is available as time-aligned context, so that I can review measured patterns without the interface asserting a cause.

### Acceptance criteria

- Glucose uses mg/dL throughout and has a directly labeled lane.
- The configured 80 mg/dL decision-support threshold is visible as a contextual line and low-region tint.
- Count, mean, median, minimum, maximum, standard deviation, below-threshold count and percentage, and gaps are available for the selected period.
- Heart-rate context shares the glucose timeline; missing heart-rate data is described as a sensor sync failure rather than a failed comparison.
- The interface explicitly states that it does not infer causation or provide a diagnosis.

## Cardiologist

### Story

As a cardiologist, I need heart-rate history, count, range, average, capture dates, and its alignment with glucose on one time axis so that I can inspect temporal context without decoding a dual-axis overlay.

### Acceptance criteria

- Heart rate uses bpm throughout and has a directly labeled, visually separate lane.
- Heart-rate statistics are calculated from the full stored sample set, not the downsampled chart points.
- The heart-rate source attribution and exact timestamps remain available in the detailed watch table.
- The display never compresses non-overlapping dates into apparent alignment.
- Current heart-rate transfer failures are visually prominent and include the timestamp of the newest successfully transferred sample.
- A heart-rate sample older than 60 seconds is never presented in the current-vital position.

## Shared clinical handoff

### Story

As any reader receiving this dashboard, I need to distinguish observation from interpretation and see when, where, and how the data arrived so that the record can support a responsible conversation.

### Acceptance criteria

- Every stream exposes measurement coverage and record counts.
- Freshness, provenance, source attribution, and ingest timing remain available.
- Printed output carries the generated timestamp and selected-period facts.
- The safety boundary remains visible and no automated treatment action is offered.

## Implementation trace

- `dashboard.py` calculates full-sample heart-rate statistics and five-minute alignment status.
- `index.html` presents current paired vitals, day/week/month/year controls, data coverage, sensor sync status, clinical facts, and print control.
- `app.js` renders the facts and synchronized two-lane timeline without causal claims.
- `app.css` provides stable desktop, mobile, and print layouts with consistent stream colors.
- Automated tests cover full-sample heart-rate statistics, year-window support, overlap calculations, stale heart-rate transfer failure detection, and the 60-second live-heart cutoff.
