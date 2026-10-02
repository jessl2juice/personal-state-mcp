# Casey Fitbit Air State Model

Document version: 1.0  
Date: 2026-10-02  
Status: Canonical builder guidance for Casey biofeedback

## Executive summary

Fitbit Air remains a viable client-wearable plan for Casey, but the value is not medical-grade live vitals. The value is a continuously worn, normal-person device that can give Casey source-labeled context about activation, recovery, sleep, activity, and trend change.

The 2026-10-02 field finding changed the design:

- Direct Fitbit Air heart rate through the standard Bluetooth LE Heart Rate Service can be live on Casey's phone after Google Health's Fitbit Share heart-rate mode is enabled.
- A real disagreement showed Galaxy Watch heart rate high while Fitbit Air stayed lower; a manual pulse check supported Fitbit. Casey must not assume Galaxy is the more accurate source.
- Fitbit cloud, Google Health, and Health Connect remain useful for sleep, activity, recovery, and corroborating history, but they are synchronized context lanes, not realtime biofeedback lanes.
- Casey must reason over trends, deltas, persistence, and signal confidence. It must not present consumer-wearable readings as diagnosis, alarm, treatment guidance, or a known cause of a client's behavior.

## Product boundary

Casey may use Personal State data to make the conversation safer and better paced. Casey may slow down, ask a clarifying question, offer a break, or ask whether the current context matches the client's felt state.

Casey must not:

- diagnose anxiety, panic, hypoglycemia, sleep disorder, cardiac risk, or any other condition from wearable data;
- claim that a reading caused a mood, decision, symptom, or behavior;
- give medication, nutrition, exercise, driving, emergency, or treatment instructions;
- hide source disagreement behind one blended number;
- silently substitute Galaxy Watch for Fitbit when Casey's client deployment assumes Fitbit Air.

Official Libre, Fitbit, Samsung, Google, and clinical systems remain the authority for their own alerts, safety notices, and care workflows.

## Source lanes

| Source lane | Adapter/source id | Timing role | Casey role |
| --- | --- | --- | --- |
| Fitbit direct Bluetooth heart rate | `fitbit_ble_heart_rate` | Live only while the newest measured sample is no more than 10 seconds old | Primary Casey realtime heart-rate lane |
| Fitbit phone Health Connect history | `android_health_connect_fitbit` | Phone-synchronized history | Corroboration, recent context, and gap analysis |
| Fitbit / Google Health cloud history | `google_health_fitbit` | Provider-synchronized history | Sleep, activity, recovery, and longer trends |
| Galaxy direct live heart rate | `wear_health_services` | Live only while the newest measured sample is no more than 10 seconds old | Optional comparison source, not a Fitbit fallback |
| Samsung Health / Health Connect history | Samsung/Health Connect adapter lanes | Recorded history | Secondary context with explicit attribution |
| Libre glucose | LibreLinkUp follower adapter | Near real time only when fresh | Separate glucose context with its own source rules |

Live is a property of one direct sample, not an entire panel. Synchronized source panels can be connected and still be useless for realtime biofeedback if their newest heart-rate record is outside the currentness window.

## Casey state estimator

The builder agent should implement this as a state-estimation layer above raw observations.

```text
raw observations
  -> source quality and recency
  -> personalized baselines
  -> trend and rate-of-change features
  -> state estimate with confidence
  -> Casey conversation policy
```

Recommended states:

- `stable`: current signals are close to the client's usual context.
- `activated`: heart rate or activity-adjusted heart rate is materially above the client's baseline and rising or persistent.
- `recovering`: heart rate is falling toward baseline after activity or activation.
- `depleted`: sleep, recovery, resting heart rate, HRV, or activity pattern suggests lower resilience than usual.
- `conflicted`: sources disagree materially or one source is implausible for the context.
- `low_confidence`: data is sparse, stale, missing, or quality-limited.
- `insufficient_data`: no meaningful source window is available.

These are conversational context states, not medical states.

## Signal features Casey can use

### Heart rate

Use:

- current Fitbit BLE heart rate when the sample age is 10 seconds or less;
- short-window slope, such as 30 seconds and 2 minutes;
- delta from the client's rolling resting or calm baseline;
- persistence above baseline, not one isolated spike;
- recovery slope after movement or a known active period;
- source conflict status when Galaxy and Fitbit disagree.

Do not use:

- a stale cloud or Health Connect record as live;
- one uncorroborated spike as a conclusion;
- a cross-device hierarchy that always prefers Galaxy or always prefers Fitbit.

### Sleep and recovery

Use sleep duration, sleep timing, fragmentation, sleep-stage summaries when available, overnight resting heart rate and HRV when available, and deviation from that client's baseline. Casey can adjust conversational load by keeping questions shorter, summarizing more often, or checking whether the client wants a pause.

### Activity and movement context

Use steps, active minutes, workouts, rest-to-active transitions, and recovery after movement stops so Casey does not misread expected movement-related heart rate as emotional activation.

### Missingness and device behavior

Use source freshness, last successful phone upload, last successful Fitbit BLE packet, reconnect events, and battery or foreground-service continuity when available. Missingness is part of the state estimate.

## Confidence model

| Confidence | Meaning | Agent behavior |
| --- | --- | --- |
| `usable` | Current Fitbit BLE or another explicitly named direct-live source is fresh and plausible | May use trend-aware language with source and age |
| `trend_only` | Recent history is present but not live | May discuss patterns, not current state |
| `context_only` | Sleep/activity/recovery context is available without current heart rate | May adjust pacing but not mention current vitals |
| `conflicted` | Sources disagree materially or one source is implausible | Must say the data is conflicting or keep it internal |
| `unusable` | Data is missing, stale, or source quality is poor | Must not use physiology as evidence |

Manual calibration events should be represented explicitly. If a client or operator checks pulse and confirms one source during a conflict, Casey may temporarily raise confidence for that source for that client/device context. The calibration does not create permanent device superiority.

## Conversation policy

Allowed language:

- "I have recent Fitbit context, and it looks like your body may be more activated than your usual calm baseline. Does that match how you feel?"
- "Your wearable context is stale, so I am not going to lean on it here."
- "The sources disagree, so I am treating the physiology as low-confidence and staying with what you tell me."
- "You slept less than your usual pattern, so I will keep this lighter and check in more often."

Disallowed language:

- "Your heart rate means you are anxious."
- "The watch says you are fine."
- "Your Fitbit proves you are stressed."
- "Take medication, eat, exercise, stop driving, or seek emergency care because Casey saw this value."

## Builder requirements

Add or preserve these model fields:

- `source_id`
- `source_label`
- `measured_at`
- `received_at` or `ingested_at_server`
- `age_seconds`
- `freshness_state`
- `source_quality`
- `state_confidence`
- `client_baseline_window`
- `delta_from_baseline`
- `short_slope`
- `persistence_seconds`
- `conflict_state`
- `manual_calibration_event`

Expose state estimates separately from raw observations. Raw values remain visible with source and age. Derived state should be marked derived and should carry the observations and policy rules used to produce it.

## Open validation work

Before declaring Casey Fitbit Air biofeedback operational:

1. Run an outdoor or gym session with only phone plus Fitbit required for Casey's source path.
2. Verify Fitbit BLE stays live, reconnects after range loss, and does not require developer tools.
3. Record battery impact for the phone and Fitbit during a representative session.
4. Confirm the dashboard shows Fitbit direct live, Fitbit synchronized context, Galaxy source, and Libre source together without blending labels.
5. Replay a source-conflict case and verify Casey reports `conflicted` or stays silent rather than choosing a hidden winner.
6. Validate at least one manual pulse calibration event and its expiry behavior.
7. Verify sleep/activity/recovery context arrives often enough to be useful even when live heart rate is absent.

## Current conclusion

The Fitbit Air plan is still viable because Casey does not need a hospital monitor for every client. Casey needs a wearable that normal people will actually wear, plus a conservative state model that turns source-labeled signals into useful conversational context without pretending to know more than the devices can support.
