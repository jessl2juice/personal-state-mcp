(() => {
  "use strict";

  const token = document.querySelector('meta[name="personal-state-token"]').content;
  const rangePreferenceVersion = "day-week-month-year-v1";
  const savedRange = localStorage.getItem("personal-state-range");
  const savedRangeVersion = localStorage.getItem("personal-state-range-version");
  const state = {
    range: savedRangeVersion === rangePreferenceVersion && ["24h", "7d", "30d", "1y"].includes(savedRange) ? savedRange : "24h",
    payload: null,
    chartPoints: [],
    chartSeries: { glucose: [], heart: [] },
    loading: false,
    liveLoading: false,
    heartCutoffTimer: null,
    detailMetric: null,
    detailRange: "24h",
    detailPayload: null,
  };

  const rangeLabels = {
    "24h": "Day · last 24 hours",
    "7d": "Week · last 7 days",
    "30d": "Month · last 30 days",
    "1y": "Year · last 12 months",
  };

  const rangeHours = { "24h": 24, "7d": 168, "30d": 720, "1y": 8760 };
  const rangeNames = { "24h": "Day", "7d": "Week", "30d": "Month", "1y": "Year" };
  const HEART_LIVE_MAX_AGE_SECONDS = 2;
  const HEART_GRAPH_GAP_MS = 60 * 1000;
  const watchMetricLabels = {
    "activity.steps": "Steps",
    "activity.exercise_calories": "Exercise calories",
    "activity.distance": "Distance",
    "activity.exercise_session": "Exercise session",
    "activity.exercise_power": "Exercise power",
    "activity.speed": "Speed",
    "activity.vo2_max": "VO2 max",
    "vitals.heart_rate": "Heart rate",
    "vitals.oxygen_saturation": "Blood oxygen",
    "vitals.oxygen_saturation_series": "Blood oxygen during sleep",
    "vitals.blood_pressure": "Blood pressure",
    "vitals.blood_glucose": "Samsung Health glucose",
    "sleep.session": "Sleep",
    "sleep.samsung_session": "Samsung sleep session",
    "sleep.summary": "Samsung sleep summary",
    "sleep.score": "Samsung sleep score",
    "sleep.apnea_detected_sign": "Samsung Health Monitor detected sign",
    "vitals.skin_temperature": "Skin temperature",
    "wellness.energy_score": "Samsung Energy Score",
    "cardiac.irregular_rhythm_notification": "Samsung irregular-rhythm notification",
    "activity.floors": "Floors",
    "activity.active_time": "Active time",
    "body.weight": "Weight",
    "body.body_fat": "Body fat",
    "body.basal_metabolic_rate": "Basal metabolic rate",
    "body.height": "Height",
    "nutrition.intake": "Nutrition",
  };
  const watchMetricOrder = [
    "vitals.heart_rate",
    "vitals.oxygen_saturation",
    "vitals.oxygen_saturation_series",
    "vitals.skin_temperature",
    "wellness.energy_score",
    "sleep.score",
    "sleep.summary",
    "sleep.samsung_session",
    "sleep.apnea_detected_sign",
    "cardiac.irregular_rhythm_notification",
    "activity.steps",
    "activity.active_time",
    "activity.floors",
    "sleep.session",
    "activity.distance",
    "activity.exercise_calories",
    "activity.speed",
    "activity.exercise_session",
    "vitals.blood_pressure",
    "vitals.blood_glucose",
    "activity.vo2_max",
    "activity.exercise_power",
    "body.weight",
    "body.body_fat",
    "body.basal_metabolic_rate",
    "body.height",
    "nutrition.intake",
  ];
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatDate(value, options = {}) {
    if (!value) return "--";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "--";
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      ...options,
    }).format(date);
  }

  function formatTime(value) {
    if (!value) return "--";
    return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(value));
  }

  function formatChartTick(value) {
    const date = new Date(value);
    if ((rangeHours[state.range] || 24) <= 24) return formatTime(value);
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      ...(state.range === "1y" ? { year: "2-digit" } : {}),
    }).format(date);
  }

  function formatAge(seconds) {
    if (seconds === null || seconds === undefined) return "Age unavailable";
    const age = Math.max(0, Number(seconds));
    if (age < 60) return `${Math.round(age)} sec old`;
    if (age < 3600) return `${Math.round(age / 60)} min old`;
    if (age < 86400) return `${(age / 3600).toFixed(age < 7200 ? 1 : 0)} hr old`;
    return `${(age / 86400).toFixed(1)} days old`;
  }

  function formatDuration(seconds) {
    const value = Math.max(0, Number(seconds || 0));
    if (value < 3600) return `${Math.round(value / 60)} minutes`;
    if (value < 86400) return `${(value / 3600).toFixed(1)} hours`;
    return `${(value / 86400).toFixed(1)} days`;
  }

  function formatCoverage(start, end) {
    if (!start || !end) return "No measurements";
    const startDate = new Date(start);
    const endDate = new Date(end);
    const sameDay = startDate.toDateString() === endDate.toDateString();
    if (sameDay) return `${formatDate(start)} to ${formatTime(end)}`;
    return `${formatDate(start)} to ${formatDate(end)}`;
  }

  function heartSampleTime(observation) {
    const samples = observation?.payload?.samples;
    return Array.isArray(samples) && samples.length
      ? samples[samples.length - 1].time
      : observationTime(observation);
  }

  function setText(selector, value) {
    const element = $(selector);
    if (element) element.textContent = value;
  }

  function showMessage(message, isError = false) {
    const element = $("#app-message");
    element.textContent = message;
    element.classList.toggle("is-error", isError);
    element.classList.toggle("is-visible", Boolean(message));
    if (message) window.setTimeout(() => element.classList.remove("is-visible"), 5000);
  }

  function setStatusClass(element, status) {
    element.className = "status-dot";
    if (status) element.classList.add(status);
  }

  function renderCurrent(payload) {
    const { reading, freshness, threshold_context: threshold } = payload.current;
    const freshnessLabel = freshness.status === "fresh" ? "Fresh reading" : `${freshness.status[0].toUpperCase()}${freshness.status.slice(1)} reading`;
    setText("#freshness-label", freshnessLabel);
    setStatusClass($("#freshness-dot"), freshness.status);
    setText("#current-value", reading ? reading.value_mg_dl : "--");
    setText("#current-time", reading ? `Measured ${formatDate(reading.measured_at)} · received ${formatTime(reading.received_at)}` : "No reading loaded");
    setText("#data-age", formatAge(freshness.measurement_age_seconds));

    const chip = $("#threshold-chip");
    chip.className = threshold.state || "";
    chip.textContent = `${threshold.threshold_mg_dl} mg/dL context`;

    const titles = {
      below: "Below threshold",
      near: "Near threshold",
      above: "Above threshold",
      unknown_stale: "Not evaluated while stale",
      unavailable: "Context unavailable",
    };
    setText("#context-title", titles[threshold.state] || "Current context unavailable");
    setText("#context-copy", threshold.message || "The official Libre app and sensor remain the safety alert layer.");

    const heart = payload.watch?.latest?.["vitals.heart_rate"];
    const heartRecency = heart?.observation_recency || {};
    const heartSync = payload.watch?.heart_rate_sync || {};
    const heartStatus = heartRecency.status === "old" ? "stale" : heartRecency.status;
    const heartPresentation = watchMetricPresentation("vitals.heart_rate", heart);
    const heartAgeSeconds = Number(heartRecency.measurement_age_seconds);
    const heartIsCurrent = Boolean(heart)
      && heartSync.status === "current"
      && Number.isFinite(heartAgeSeconds)
      && heartAgeSeconds <= HEART_LIVE_MAX_AGE_SECONDS;
    const heartCard = $("#current-heart-card");
    const heartRepairAction = $("#heart-repair-action");
    if (state.heartCutoffTimer) {
      window.clearTimeout(state.heartCutoffTimer);
      state.heartCutoffTimer = null;
    }
    heartCard.classList.toggle("is-unavailable", !heartIsCurrent);
    heartRepairAction.hidden = heartIsCurrent;
    setStatusClass($("#heart-freshness-dot"), heartIsCurrent ? heartStatus : "error");
    setText("#heart-freshness-label", heartIsCurrent
      ? `Live · ${formatAge(heartRecency.measurement_age_seconds)}`
      : heartSync.status === "failure" ? "Sync failed · no live heart rate" : "No live heart-rate data");
    setText("#current-heart-value", heartIsCurrent ? heartPresentation.value : "No live data");
    setText("#current-heart-unit", heartIsCurrent ? "bpm" : "");
    setText("#current-heart-time", heartIsCurrent
      ? `Measured ${formatDate(heartSampleTime(heart))} · uploaded ${formatDate(heart.ingested_at_server)}`
      : heart
        ? `Historical only: ${heartPresentation.value} bpm recorded ${formatDate(heartSampleTime(heart))} (${formatAge(heartRecency.measurement_age_seconds)})`
        : "No heart-rate sample has been received");

    if (!heartIsCurrent) {
      setText("#rail-status", heartSync.status === "failure" ? "Heart-rate sync failed" : "Heart-rate data unavailable");
      setStatusClass($("#rail-status-dot"), "error");
      setText("#rail-updated", reading ? `Glucose ${freshness.status} · heart rate unavailable` : "Current sensor data unavailable");
    } else {
      setText("#rail-status", freshnessLabel);
      setStatusClass($("#rail-status-dot"), freshness.status);
      setText("#rail-updated", reading ? `Measured ${formatTime(reading.measured_at)}` : "No reading available");
      const cutoffDelay = Math.max(0, HEART_LIVE_MAX_AGE_SECONDS - heartAgeSeconds) * 1000 + 50;
      state.heartCutoffTimer = window.setTimeout(() => {
        heartCard.classList.add("is-unavailable");
        setStatusClass($("#heart-freshness-dot"), "error");
        setText("#heart-freshness-label", "Failed · latest sample is over 2 seconds old");
        setText("#current-heart-value", "No live data");
        setText("#current-heart-unit", "");
        setText("#current-heart-time", `Historical only: ${heartPresentation.value} bpm recorded ${formatDate(heartSampleTime(heart))}`);
        setText("#rail-status", "Heart-rate data not live");
        setStatusClass($("#rail-status-dot"), "error");
        setText("#rail-updated", "Live heart rate is unavailable");
        loadLive({ quiet: true });
      }, cutoffDelay);
    }
  }

  function renderClinicalReview(payload) {
    const stats = payload.stats || {};
    const heart = payload.watch?.heart_rate_summary || {};
    const heartSync = payload.watch?.heart_rate_sync || {};
    const glucoseCount = Number(stats.count || 0);
    const heartCount = Number(heart.count || 0);
    const value = (number, suffix = "") => number === null || number === undefined ? "--" : `${number}${suffix}`;

    setText("#report-generated", `Generated ${formatDate(payload.generated_at)}`);
    setText("#glucose-coverage", formatCoverage(payload.range.start, payload.range.end));
    setText("#glucose-coverage-count", `${glucoseCount.toLocaleString()} ${glucoseCount === 1 ? "reading" : "readings"}`);
    setText("#heart-coverage", formatCoverage(heart.first_at, heart.last_at));
    setText("#heart-coverage-count", `${heartCount.toLocaleString()} ${heartCount === 1 ? "sample" : "samples"}`);

    const chip = $("#comparison-chip");
    const syncFailure = heartSync.status === "failure";
    chip.className = `comparison-chip ${syncFailure ? "no_overlap" : "same-clock"}`;
    chip.textContent = syncFailure ? "Heart-rate sync failure" : "Same person · Shared clock";
    setText("#comparison-title", rangeNames[state.range] || "Selected period");
    const windowMessage = heartCount
      ? `${glucoseCount ? "Both sensor streams are shown" : "Heart-rate data is shown"} on the same recorded-time axis.`
      : `Heart-rate data has not synced for this ${String(rangeNames[state.range] || "period").toLowerCase()}.`;
    const syncMessage = heartSync.last_sample_at
      ? `Current heart-rate transfer failed. Last recorded sample: ${formatDate(heartSync.last_sample_at)} (${formatAge(heartSync.sample_age_seconds)}).`
      : "Current heart-rate transfer failed. No heart-rate sample has been received.";
    setText("#comparison-detail", syncFailure ? `${windowMessage} ${syncMessage}` : windowMessage);

    setText("#fact-glucose-count", glucoseCount.toLocaleString());
    setText("#fact-glucose-average", value(stats.average_mg_dl));
    setText("#fact-glucose-median", value(stats.median_mg_dl));
    setText("#fact-glucose-range", stats.minimum_mg_dl === null || stats.minimum_mg_dl === undefined ? "--" : `${stats.minimum_mg_dl}–${stats.maximum_mg_dl}`);
    setText("#fact-glucose-below", stats.below_threshold_percent === null || stats.below_threshold_percent === undefined ? "--" : `${stats.below_threshold} (${stats.below_threshold_percent}%)`);
    setText("#fact-glucose-variability", value(stats.standard_deviation_mg_dl));
    setText("#fact-heart-count", heartCount.toLocaleString());
    setText("#fact-heart-average", value(heart.average));
    setText("#fact-heart-range", heart.minimum === null || heart.minimum === undefined ? "--" : `${heart.minimum}–${heart.maximum}`);
    setText("#fact-heart-first", formatDate(heart.first_at));
    setText("#fact-heart-last", formatDate(heart.last_at));
    setText("#fact-heart-sync", syncFailure ? "Failed" : heartSync.status === "current" ? "Current" : "Stale");
    setText("#comparison-fact-title", syncFailure ? "Heart-rate sync failure" : "Same-person sensor timeline");
    setText("#comparison-fact-copy", `Glucose and heart rate use the same recorded-time axis. ${syncFailure ? syncMessage : (heartSync.message || windowMessage)} This view reports recorded measurements and does not infer causation.`);
  }

  function renderStats(payload) {
    const stats = payload.stats;
    const value = (number) => number === null || number === undefined ? "--" : number;
    setText("#stat-average", value(stats.average_mg_dl));
    setText("#stat-minimum", value(stats.minimum_mg_dl));
    setText("#stat-maximum", value(stats.maximum_mg_dl));
    setText("#stat-below", value(stats.below_threshold));
    setText("#stat-below-detail", stats.below_threshold_percent === null ? "readings" : `${stats.below_threshold_percent}% of readings`);
    setText("#stat-median", stats.median_mg_dl === null ? "--" : `${stats.median_mg_dl} mg/dL`);
    setText("#stat-variability", stats.standard_deviation_mg_dl === null ? "--" : `${stats.standard_deviation_mg_dl} mg/dL`);
    setText("#gap-count", String(payload.gaps.length));
    setText("#reading-count", `${stats.count.toLocaleString()} ${stats.count === 1 ? "reading" : "readings"}`);

    const counts = [stats.below_threshold, stats.near_threshold, stats.above_near_band];
    const total = Math.max(1, stats.count);
    ["below", "near", "above"].forEach((name, index) => {
      setText(`#band-${name}-count`, counts[index].toLocaleString());
      $(`#band-${name}-bar`).style.width = `${(counts[index] / total) * 100}%`;
    });
  }

  function renderHistory(payload) {
    setText("#history-total", `${payload.history.count.toLocaleString()} stored readings`);
    setText("#history-first", formatDate(payload.history.first_measured_at, { year: "numeric" }));
    setText("#history-last", formatDate(payload.history.last_measured_at, { year: "numeric" }));
    if (payload.range.start && payload.range.end) {
      const hours = (new Date(payload.range.end) - new Date(payload.range.start)) / 3600000;
      setText("#history-coverage", hours < 48 ? `${hours.toFixed(1)} hours` : `${(hours / 24).toFixed(1)} days`);
    } else {
      setText("#history-coverage", "--");
    }

    const gapList = $("#gap-list");
    if (!payload.gaps.length) {
      gapList.innerHTML = '<div class="gap-empty">No gaps longer than 30 minutes in this range.</div>';
      return;
    }
    gapList.innerHTML = payload.gaps.map((gap) => `
      <div class="gap-item">
        <div><strong>${escapeHtml(formatDate(gap.from))} to ${escapeHtml(formatDate(gap.to))}</strong><br><span>Missing interval</span></div>
        <strong>${escapeHtml(formatDuration(gap.gap_seconds))}</strong>
      </div>
    `).join("");
  }

  function renderTable(payload) {
    const body = $("#readings-body");
    if (!payload.table.length) {
      body.innerHTML = '<tr><td colspan="5">No readings in this range.</td></tr>';
      return;
    }
    body.innerHTML = payload.table.map((reading) => `
      <tr>
        <td>${escapeHtml(formatDate(reading.measured_at))}</td>
        <td class="value-cell">${escapeHtml(reading.value_mg_dl)} mg/dL</td>
        <td>${escapeHtml(reading.trend || (reading.trend_raw ? `Vendor code ${reading.trend_raw}` : "--"))}</td>
        <td>${escapeHtml(formatTime(reading.received_at))}</td>
        <td><span class="type-label">${escapeHtml(reading.sample_type)}</span></td>
      </tr>
    `).join("");
  }

  function renderSystem(payload) {
    const runs = payload.collector.runs || [];
    const latest = payload.collector.last_run;
    const statusChip = $("#collector-status");
    statusChip.textContent = latest ? latest.status : "No runs";
    statusChip.className = `state-chip ${latest?.status === "ok" ? "ok" : latest ? "below" : ""}`;

    const runList = $("#collector-runs");
    runList.innerHTML = runs.length ? runs.map((run) => `
      <div class="run-item ${run.status === "ok" ? "" : "error"}">
        <span class="run-state"></span>
        <div><strong>${escapeHtml(run.status === "ok" ? "Collection completed" : "Collection failed")}</strong><span>${escapeHtml(formatDate(run.finished_at_utc))}</span></div>
        <span>${escapeHtml(run.readings_inserted)} new / ${escapeHtml(run.readings_seen)} seen</span>
      </div>
    `).join("") : '<div class="gap-empty">No collector runs recorded.</div>';

    const provenance = payload.provenance || {};
    const reading = payload.current.reading;
    setText("#prov-adapter", provenance.adapter || "--");
    setText("#prov-vendor", provenance.vendor || "--");
    setText("#prov-source", provenance.source || "--");
    setText("#prov-time", reading ? formatDate(reading.measured_at) : "--");
    setText("#prov-received", reading ? formatDate(reading.received_at) : "--");
    setText("#prov-host", provenance.selected_region_host ? new URL(provenance.selected_region_host).host : "--");
  }

  function attributionLabel(observation) {
    const state = observation?.provenance?.attribution?.state;
    const labels = {
      watch_confirmed: "Watch5 Pro confirmed",
      samsung_health_unattributed: "Samsung Health, device unconfirmed",
      phone: "Phone",
      manual: "Manual entry",
      external_device: "External device",
      unknown: "Unknown source",
    };
    return labels[state] || "Source unavailable";
  }

  function adapterLabel(observation) {
    const adapter = observation?.provenance?.adapter;
    const labels = {
      android_samsung_health_data: "Samsung Health Data SDK",
      android_health_connect: "Samsung Health via Health Connect",
      wear_health_services: "Direct Galaxy Watch",
    };
    return labels[adapter] || adapter || "Source unavailable";
  }

  function observationTime(observation) {
    return observation?.measured_at || observation?.end_at || observation?.start_at || null;
  }

  function readableNumber(value, maximumFractionDigits = 1) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "--";
    return new Intl.NumberFormat(undefined, { maximumFractionDigits }).format(number);
  }

  function watchMetricPresentation(metric, observation) {
    const payload = observation?.payload || {};
    const lastSample = Array.isArray(payload.samples) && payload.samples.length ? payload.samples[payload.samples.length - 1] : null;
    if (["sleep.session", "sleep.samsung_session", "sleep.summary"].includes(metric)) {
      const hours = observation?.start_at && observation?.end_at
        ? (new Date(observation.end_at) - new Date(observation.start_at)) / 3600000
        : null;
      return { value: hours === null ? "Recorded" : readableNumber(hours), unit: hours === null ? "" : "hours" };
    }
    if (metric === "cardiac.irregular_rhythm_notification") {
      return { value: payload.status === "detected" ? "Detected" : "Undefined", unit: "" };
    }
    if (metric === "sleep.apnea_detected_sign") {
      const labels = { detected: "Detected", not_detected: "Not detected", undefined: "Undefined" };
      return { value: labels[payload.status] || "No recorded result", unit: "" };
    }
    if (["sleep.score", "wellness.energy_score"].includes(metric)) {
      return { value: readableNumber(payload.value, 0), unit: metric === "wellness.energy_score" ? "score" : "/ 100" };
    }
    if (metric === "activity.floors") return { value: readableNumber(payload.value, 0), unit: "floors" };
    if (metric === "activity.active_time") return { value: readableNumber(payload.value, 0), unit: "minutes" };
    if (metric === "activity.distance" && payload.unit === "m") {
      const meters = Number(payload.value);
      return meters >= 1000
        ? { value: readableNumber(meters / 1000, 2), unit: "km" }
        : { value: readableNumber(meters, 0), unit: "m" };
    }
    if (metric === "activity.exercise_session") {
      return { value: payload.title || "Recorded", unit: "" };
    }
    if (metric === "vitals.blood_pressure" && payload.systolic !== undefined) {
      return { value: `${readableNumber(payload.systolic, 0)}/${readableNumber(payload.diastolic, 0)}`, unit: payload.unit || "mmHg" };
    }
    const value = lastSample?.value ?? payload.value;
    const unit = lastSample?.unit || payload.unit || "";
    return { value: readableNumber(value), unit: unit === "count" ? "steps" : unit };
  }

  function renderWatchMetricCards(latest) {
    const grid = $("#watch-latest-grid");
    const metrics = Object.keys(latest).filter((metric) => metric !== "vitals.heart_rate").sort((left, right) => {
      const leftIndex = watchMetricOrder.indexOf(left);
      const rightIndex = watchMetricOrder.indexOf(right);
      return (leftIndex < 0 ? 999 : leftIndex) - (rightIndex < 0 ? 999 : rightIndex) || left.localeCompare(right);
    });
    setText("#signal-count", `${metrics.length + (latest["vitals.heart_rate"] ? 2 : 1)} connected signals`);
    if (!metrics.length) {
      grid.innerHTML = '<div class="available-empty">No watch measurements have been recorded yet.</div>';
      return;
    }

    const metricGroup = (metric) => {
      if (["sleep.session", "sleep.samsung_session", "sleep.summary", "sleep.score", "vitals.oxygen_saturation_series", "vitals.skin_temperature", "wellness.energy_score"].includes(metric)) return "recovery";
      if (["activity.steps", "activity.active_time", "activity.floors", "activity.distance", "activity.exercise_calories", "activity.speed", "activity.exercise_session", "activity.exercise_power", "activity.vo2_max"].includes(metric)) return "activity";
      if (["cardiac.irregular_rhythm_notification", "sleep.apnea_detected_sign"].includes(metric)) return "findings";
      return "health";
    };
    const groupDetails = {
      recovery: { label: "Recovery", icon: "icon-moon", description: "Last night and latest recovery records" },
      activity: { label: "Activity", icon: "icon-steps", description: "Movement recorded today or most recently" },
      findings: { label: "Samsung findings", icon: "icon-shield", description: "Vendor-reported notices, not diagnoses" },
      health: { label: "Other health", icon: "icon-layers", description: "Latest available connected records" },
    };
    const metricIcons = {
      "vitals.oxygen_saturation": "icon-wind",
      "vitals.oxygen_saturation_series": "icon-wind",
      "vitals.skin_temperature": "icon-thermometer",
      "wellness.energy_score": "icon-zap",
      "sleep.score": "icon-moon",
      "sleep.summary": "icon-moon",
      "sleep.samsung_session": "icon-moon",
      "sleep.session": "icon-moon",
      "sleep.apnea_detected_sign": "icon-shield",
      "cardiac.irregular_rhythm_notification": "icon-heart",
      "activity.steps": "icon-steps",
      "activity.active_time": "icon-clock",
      "activity.floors": "icon-layers",
      "activity.distance": "icon-steps",
      "activity.exercise_calories": "icon-zap",
      "activity.speed": "icon-gauge",
      "activity.exercise_session": "icon-activity",
      "activity.exercise_power": "icon-zap",
      "activity.vo2_max": "icon-wind",
      "vitals.blood_pressure": "icon-gauge",
      "vitals.blood_glucose": "icon-droplet",
    };
    const groups = { recovery: [], activity: [], findings: [], health: [] };
    metrics.forEach((metric) => groups[metricGroup(metric)].push(metric));

    const cardMarkup = (metric) => {
      const observation = latest[metric];
      const presentation = watchMetricPresentation(metric, observation);
      const ageSeconds = Number(observation.observation_recency?.measurement_age_seconds);
      const age = formatAge(ageSeconds);
      const ageTone = Number.isFinite(ageSeconds) && ageSeconds <= 60 ? "live" : Number.isFinite(ageSeconds) && ageSeconds <= 86400 ? "today" : "history";
      const label = watchMetricLabels[metric] || metric;
      const finding = ["cardiac.irregular_rhythm_notification", "sleep.apnea_detected_sign"].includes(metric);
      const eventLabel = observation.local_date || formatDate(observationTime(observation));
      return `
        <button type="button" class="health-metric-card ${finding ? "vendor-finding-card" : ""}" data-metric="${escapeHtml(metric)}" aria-label="Open ${escapeHtml(label)} history and graph">
          <div class="metric-card-top"><span class="metric-icon"><svg><use href="#${metricIcons[metric] || "icon-activity"}"/></svg></span><span class="metric-age ${ageTone}">${escapeHtml(age)}</span></div>
          <span class="metric-label">${escapeHtml(label)}</span>
          <div class="metric-value"><strong>${escapeHtml(presentation.value)}</strong>${presentation.unit ? `<small>${escapeHtml(presentation.unit)}</small>` : ""}</div>
          <p class="metric-meta">${escapeHtml(adapterLabel(observation))}</p>
          <p class="metric-time">Recorded ${escapeHtml(eventLabel)}</p>
        </button>
      `;
    };

    grid.innerHTML = Object.entries(groups).filter(([, groupMetrics]) => groupMetrics.length).map(([groupName, groupMetrics]) => {
      const detail = groupDetails[groupName];
      return `
        <section class="signal-group signal-${groupName}">
          <div class="signal-group-heading"><span class="signal-group-icon"><svg><use href="#${detail.icon}"/></svg></span><div><h3>${detail.label}</h3><p>${detail.description}</p></div></div>
          <div class="signal-cards">${groupMetrics.map(cardMarkup).join("")}</div>
        </section>
      `;
    }).join("");
  }

  function renderWatch(payload) {
    const watch = payload.watch || {};
    const latest = watch.latest || {};
    const status = $("#watch-status-chip");
    if (!watch.enabled) {
      status.textContent = "Setup pending";
      status.className = "state-chip";
    } else if (!watch.configured) {
      status.textContent = "Pairing required";
      status.className = "state-chip near";
    } else if (watch.last_upload) {
      status.textContent = "Connected";
      status.className = "state-chip ok";
    } else {
      status.textContent = "Ready for first sync";
      status.className = "state-chip";
    }

    setText("#watch-read-time", watch.last_upload ? formatDate(watch.last_upload.generated_at_utc) : "No read recorded");
    setText("#watch-upload-time", watch.last_upload ? formatDate(watch.last_upload.received_at_utc) : "No upload recorded");
    setText("#watch-history-count", `${Number(watch.history?.count || 0).toLocaleString()} observations`);
    setText("#watch-retention", `${Number(watch.retention_days || 0).toLocaleString()} days`);
    setText("#watch-authority", watch.authoritative_source || "Samsung Health remains authoritative.");
    renderWatchMetricCards(latest);

    const body = $("#watch-readings-body");
    const recent = watch.recent || [];
    body.innerHTML = recent.length ? recent.map((observation) => {
      const presentation = watchMetricPresentation(observation.metric, observation);
      return `
        <tr>
          <td>${escapeHtml(formatDate(observationTime(observation)))}</td>
          <td>${escapeHtml(watchMetricLabels[observation.metric] || observation.metric)}</td>
          <td class="value-cell">${escapeHtml(presentation.value)} ${escapeHtml(presentation.unit)}</td>
          <td><strong>${escapeHtml(adapterLabel(observation))}</strong><br><span class="table-subtext">${escapeHtml(attributionLabel(observation))}</span></td>
          <td>${escapeHtml(formatDate(observation.observed_by_companion_at))}</td>
          <td>${escapeHtml(formatDate(observation.ingested_at_server))}</td>
        </tr>
      `;
    }).join("") : '<tr><td colspan="6">No watch observations uploaded yet.</td></tr>';

    const availabilityBody = $("#watch-availability-body");
    const availability = watch.availability_by_adapter || [];
    availabilityBody.innerHTML = availability.length ? availability.map((item) => `
      <tr>
        <td>${escapeHtml(watchMetricLabels[item.metric] || item.metric)}</td>
        <td>${escapeHtml(({ android_samsung_health_data: "Samsung Health Data SDK", android_health_connect: "Health Connect", wear_health_services: "Direct Galaxy Watch" })[item.adapter_id] || item.adapter_id)}</td>
        <td><span class="availability-state ${item.stale ? "is-stale" : ""}">${escapeHtml(String(item.state || "unknown").replaceAll("_", " "))}</span></td>
        <td>${escapeHtml(item.checked_at_utc ? formatDate(item.checked_at_utc) : "No report")}</td>
      </tr>
    `).join("") : '<tr><td colspan="4">No active companion coverage reports yet.</td></tr>';
  }

  function smoothSegmentForDisplay(series, radius) {
    if (radius < 1 || series.length < 3) return series;
    return series.map((point, index) => {
      if (index === 0 || index === series.length - 1) return point;
      let weightedY = 0;
      let totalWeight = 0;
      const start = Math.max(0, index - radius);
      const end = Math.min(series.length - 1, index + radius);
      for (let neighbor = start; neighbor <= end; neighbor += 1) {
        const weight = radius + 1 - Math.abs(neighbor - index);
        weightedY += series[neighbor].y * weight;
        totalWeight += weight;
      }
      return { ...point, y: weightedY / totalWeight };
    });
  }

  function strokeMonotoneSeries(ctx, points, gapLimitForPair, smoothingRadius = 0) {
    const segments = [];
    let segment = [];
    points.forEach((point, index) => {
      const previous = points[index - 1];
      const gap = previous ? point.time - previous.time : 0;
      const startsNewSegment = !previous
        || point.x <= previous.x
        || gap > gapLimitForPair(previous, point);
      if (startsNewSegment && segment.length) {
        segments.push(segment);
        segment = [];
      }
      segment.push(point);
    });
    if (segment.length) segments.push(segment);

    ctx.beginPath();
    segments.forEach((rawSeries) => {
      const series = smoothSegmentForDisplay(rawSeries, smoothingRadius);
      ctx.moveTo(series[0].x, series[0].y);
      if (series.length === 1) return;
      if (series.length === 2) {
        ctx.lineTo(series[1].x, series[1].y);
        return;
      }

      const widths = [];
      const slopes = [];
      for (let index = 0; index < series.length - 1; index += 1) {
        const width = Math.max(0.0001, series[index + 1].x - series[index].x);
        widths.push(width);
        slopes.push((series[index + 1].y - series[index].y) / width);
      }

      const tangents = new Array(series.length);
      tangents[0] = slopes[0];
      tangents[tangents.length - 1] = slopes[slopes.length - 1];
      for (let index = 1; index < series.length - 1; index += 1) {
        const before = slopes[index - 1];
        const after = slopes[index];
        if (before === 0 || after === 0 || before * after <= 0) {
          tangents[index] = 0;
          continue;
        }
        const beforeWidth = widths[index - 1];
        const afterWidth = widths[index];
        const firstWeight = (2 * afterWidth) + beforeWidth;
        const secondWeight = afterWidth + (2 * beforeWidth);
        tangents[index] = (firstWeight + secondWeight)
          / ((firstWeight / before) + (secondWeight / after));
      }

      for (let index = 0; index < series.length - 1; index += 1) {
        const current = series[index];
        const next = series[index + 1];
        const width = widths[index];
        ctx.bezierCurveTo(
          current.x + width / 3,
          current.y + (tangents[index] * width) / 3,
          next.x - width / 3,
          next.y - (tangents[index + 1] * width) / 3,
          next.x,
          next.y,
        );
      }
    });
    ctx.stroke();
  }

  function drawChart() {
    const canvas = $("#glucose-chart");
    const wrap = $("#chart-wrap");
    const glucosePoints = state.payload?.chart || [];
    const heartPoints = state.payload?.watch?.heart_rate_samples || [];
    $("#chart-heart-label").textContent = heartPoints.length ? "Heart rate · smoothed" : "Heart rate (none)";
    $("#chart-empty").hidden = glucosePoints.length > 0 || heartPoints.length > 0;
    if (!glucosePoints.length && !heartPoints.length) {
      const context = canvas.getContext("2d");
      context.clearRect(0, 0, canvas.width, canvas.height);
      state.chartPoints = [];
      state.chartSeries = { glucose: [], heart: [] };
      return;
    }

    const rect = wrap.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(280, rect.width - 16);
    const height = Math.max(360, rect.height - 16);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);

    const margins = { top: 34, right: 18, bottom: 38, left: 62 };
    const plotWidth = width - margins.left - margins.right;
    const availableHeight = height - margins.top - margins.bottom;
    const laneGap = 48;
    const glucoseHeight = Math.round((availableHeight - laneGap) * 0.57);
    const heartHeight = availableHeight - laneGap - glucoseHeight;
    const glucoseTop = margins.top;
    const heartTop = glucoseTop + glucoseHeight + laneGap;
    const glucoseTimes = glucosePoints.map((point) => new Date(point.measured_at).getTime());
    const heartTimes = heartPoints.map((point) => new Date(point.time).getTime());
    const timestamps = [...glucoseTimes, ...heartTimes];
    const values = glucosePoints.map((point) => point.value_mg_dl);
    const threshold = state.payload.settings.decision_support_threshold_mg_dl;
    const requestedStart = new Date(state.payload.range.window_start).getTime();
    const requestedEnd = new Date(state.payload.range.window_end).getTime();
    const minX = Number.isFinite(requestedStart) ? requestedStart : Math.min(...timestamps);
    const maxX = Number.isFinite(requestedEnd) ? requestedEnd : Math.max(...timestamps);
    const rawMin = Math.min(...(values.length ? values : [threshold]), threshold);
    const rawMax = Math.max(...(values.length ? values : [threshold + 10]), threshold + 10);
    const minY = Math.max(35, Math.floor((rawMin - 18) / 10) * 10);
    const maxY = Math.ceil((rawMax + 18) / 10) * 10;
    const xScale = (time) => margins.left + ((time - minX) / Math.max(1, maxX - minX)) * plotWidth;
    const yScale = (value) => glucoseTop + ((maxY - value) / Math.max(1, maxY - minY)) * glucoseHeight;
    const heartValues = heartPoints.map((point) => Number(point.value));
    const heartMinY = heartValues.length ? Math.max(20, Math.floor((Math.min(...heartValues) - 12) / 10) * 10) : 40;
    const heartMaxY = heartValues.length ? Math.min(260, Math.ceil((Math.max(...heartValues) + 12) / 10) * 10) : 180;
    const heartYScale = (value) => heartTop + ((heartMaxY - value) / Math.max(1, heartMaxY - heartMinY)) * heartHeight;

    ctx.fillStyle = "#f8fbff";
    ctx.fillRect(margins.left, glucoseTop, plotWidth, glucoseHeight);
    ctx.fillRect(margins.left, heartTop, plotWidth, heartHeight);
    ctx.fillStyle = "#fff0ee";
    ctx.fillRect(margins.left, yScale(threshold), plotWidth, glucoseTop + glucoseHeight - yScale(threshold));

    ctx.font = "11px Roboto, Arial, sans-serif";
    ctx.fillStyle = "#64748b";
    ctx.strokeStyle = "#e3eaf4";
    ctx.lineWidth = 1;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    const tickCount = 3;
    for (let index = 0; index <= tickCount; index += 1) {
      const glucoseValue = minY + ((maxY - minY) / tickCount) * index;
      const glucoseY = yScale(glucoseValue);
      ctx.beginPath();
      ctx.moveTo(margins.left, glucoseY);
      ctx.lineTo(width - margins.right, glucoseY);
      ctx.stroke();
      ctx.fillText(String(Math.round(glucoseValue)), margins.left - 10, glucoseY);

      const heartValue = heartMinY + ((heartMaxY - heartMinY) / tickCount) * index;
      const heartY = heartYScale(heartValue);
      ctx.beginPath();
      ctx.moveTo(margins.left, heartY);
      ctx.lineTo(width - margins.right, heartY);
      ctx.stroke();
      ctx.fillText(String(Math.round(heartValue)), margins.left - 10, heartY);
    }

    const xTicks = width < 520 ? 3 : 5;
    for (let index = 0; index < xTicks; index += 1) {
      const ratio = index / (xTicks - 1);
      const time = minX + (maxX - minX) * ratio;
      const x = margins.left + plotWidth * ratio;
      ctx.strokeStyle = "#edf2f8";
      ctx.beginPath();
      ctx.moveTo(x, glucoseTop);
      ctx.lineTo(x, heartTop + heartHeight);
      ctx.stroke();
      ctx.fillStyle = "#64748b";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillText(formatChartTick(new Date(time).toISOString()), x, heartTop + heartHeight + 11);
    }

    const thresholdY = yScale(threshold);
    ctx.strokeStyle = "#e35d4f";
    ctx.setLineDash([6, 5]);
    ctx.beginPath();
    ctx.moveTo(margins.left, thresholdY);
    ctx.lineTo(width - margins.right, thresholdY);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.font = "700 11px Roboto, Arial, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    ctx.fillStyle = "#07856f";
    ctx.fillText("GLUCOSE  mg/dL", margins.left, glucoseTop - 10);
    ctx.fillStyle = "#d43d64";
    ctx.fillText("HEART RATE  bpm", margins.left, heartTop - 10);
    ctx.font = "10px Roboto, Arial, sans-serif";
    ctx.textAlign = "right";
    ctx.fillStyle = "#b8453b";
    ctx.fillText(`${threshold} context threshold`, width - margins.right, thresholdY - 6);

    const coords = glucosePoints.map((point, index) => ({
      x: xScale(glucoseTimes[index]),
      y: yScale(point.value_mg_dl),
      point,
      kind: "glucose",
      time: glucoseTimes[index],
    }));
    ctx.strokeStyle = "#07856f";
    ctx.lineWidth = 2.7;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.beginPath();
    coords.forEach((coord, index) => {
      const previous = coords[index - 1];
      const gap = previous ? coord.time - previous.time : 0;
      if (!previous || gap > 30 * 60 * 1000) ctx.moveTo(coord.x, coord.y);
      else ctx.lineTo(coord.x, coord.y);
    });
    ctx.stroke();

    const heartCoords = heartPoints.map((point, index) => ({
      x: xScale(heartTimes[index]),
      y: heartYScale(Number(point.value)),
      point,
      kind: "heart",
      time: heartTimes[index],
    }));
    ctx.strokeStyle = "#d43d64";
    ctx.lineWidth = 2.4;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    strokeMonotoneSeries(ctx, heartCoords, () => HEART_GRAPH_GAP_MS, 3);

    [
      { points: coords, color: "#07856f" },
      { points: heartCoords, color: "#d43d64" },
    ].forEach((series) => {
      if (!series.points.length) return;
      const last = series.points[series.points.length - 1];
      ctx.fillStyle = "#ffffff";
      ctx.strokeStyle = series.color;
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.arc(last.x, last.y, 4.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    });

    ctx.font = "12px Roboto, Arial, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = "#8996aa";
    if (!glucosePoints.length) ctx.fillText("No glucose measurements in this range", margins.left + plotWidth / 2, glucoseTop + glucoseHeight / 2);
    if (!heartPoints.length) ctx.fillText(`Heart-rate sync failure · no samples in this ${String(rangeNames[state.range] || "period").toLowerCase()}`, margins.left + plotWidth / 2, heartTop + heartHeight / 2);

    state.chartPoints = [...coords, ...heartCoords];
    state.chartSeries = { glucose: coords, heart: heartCoords };
  }

  function render(payload) {
    state.payload = payload;
    renderCurrent(payload);
    renderStats(payload);
    renderHistory(payload);
    renderTable(payload);
    renderSystem(payload);
    renderWatch(payload);
    renderClinicalReview(payload);
    setText("#range-caption", rangeLabels[state.range]);
    setText("#prompt-range", `${rangeLabels[state.range]} + current state`);
    $$("#range-control button").forEach((button) => button.classList.toggle("is-active", button.dataset.range === state.range));
    drawChart();
  }

  function drawMetricHistory() {
    const canvas = $("#metric-history-chart");
    const wrap = $("#metric-history-wrap");
    const points = state.detailPayload?.points || [];
    const empty = $("#metric-history-empty");
    const rect = wrap.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    empty.hidden = points.length > 0;
    if (!points.length) return;

    const margin = { left: 52, right: 20, top: 22, bottom: 38 };
    const width = Math.max(1, rect.width - margin.left - margin.right);
    const height = Math.max(1, rect.height - margin.top - margin.bottom);
    const times = points.map((point) => new Date(point.time).getTime()).filter(Number.isFinite);
    const values = points.map((point) => Number(point.value)).filter(Number.isFinite);
    if (!times.length || !values.length) return;
    const minTime = Math.min(...times);
    const maxTime = Math.max(...times);
    const rawMin = Math.min(...values);
    const rawMax = Math.max(...values);
    const pad = Math.max((rawMax - rawMin) * .12, rawMax === rawMin ? Math.max(Math.abs(rawMax) * .05, 1) : 1);
    const minValue = rawMin - pad;
    const maxValue = rawMax + pad;
    const x = (time) => margin.left + ((time - minTime) / Math.max(1, maxTime - minTime)) * width;
    const y = (value) => margin.top + (1 - (value - minValue) / Math.max(1, maxValue - minValue)) * height;

    ctx.strokeStyle = "#dfe3eb";
    ctx.lineWidth = 1;
    ctx.fillStyle = "#5f6368";
    ctx.font = '11px "Google Sans", Roboto, Arial, sans-serif';
    for (let index = 0; index <= 4; index += 1) {
      const py = margin.top + (height * index / 4);
      const value = maxValue - ((maxValue - minValue) * index / 4);
      ctx.beginPath(); ctx.moveTo(margin.left, py); ctx.lineTo(margin.left + width, py); ctx.stroke();
      ctx.textAlign = "right"; ctx.textBaseline = "middle"; ctx.fillText(readableNumber(value), margin.left - 8, py);
    }
    ctx.strokeStyle = "#0b57d0";
    ctx.lineWidth = 2.5;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.beginPath();
    points.forEach((point, index) => {
      const px = x(new Date(point.time).getTime());
      const py = y(Number(point.value));
      if (index === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    });
    ctx.stroke();
    ctx.fillStyle = "#5f6368";
    ctx.textBaseline = "top";
    ctx.textAlign = "left"; ctx.fillText(formatDate(new Date(minTime).toISOString(), { year: "numeric" }), margin.left, margin.top + height + 12);
    ctx.textAlign = "right"; ctx.fillText(formatDate(new Date(maxTime).toISOString(), { year: "numeric" }), margin.left + width, margin.top + height + 12);
  }

  function renderMetricHistory(payload) {
    state.detailPayload = payload;
    const observations = payload.observations || [];
    const latestObservation = observations[0];
    const latestPoint = (payload.points || []).at(-1);
    const presentation = latestObservation ? watchMetricPresentation(payload.metric, latestObservation) : null;
    setText("#metric-detail-value", presentation ? `${presentation.value}${presentation.unit ? ` ${presentation.unit}` : ""}` : latestPoint ? `${readableNumber(latestPoint.value)} ${latestPoint.unit || ""}`.trim() : "No recorded value");
    setText("#metric-detail-age", latestObservation ? formatAge(latestObservation.observation_recency?.measurement_age_seconds) : "No observation");
    setText("#metric-detail-source", latestObservation ? adapterLabel(latestObservation) : "No source");
    setText("#metric-detail-status", `${(payload.points || []).length.toLocaleString()} plotted samples · ${observations.length.toLocaleString()} recent records`);
    $("#metric-history-body").innerHTML = observations.length ? observations.map((observation) => {
      const value = watchMetricPresentation(payload.metric, observation);
      return `<tr><td>${escapeHtml(formatDate(observationTime(observation), { year: "numeric", second: "2-digit" }))}</td><td>${escapeHtml(`${value.value}${value.unit ? ` ${value.unit}` : ""}`)}</td><td>${escapeHtml(adapterLabel(observation))}</td><td>${escapeHtml(formatAge(observation.observation_recency?.measurement_age_seconds))}</td></tr>`;
    }).join("") : '<tr><td colspan="4">No records in this period.</td></tr>';
    $$("#metric-range-control button").forEach((button) => button.classList.toggle("is-active", button.dataset.range === state.detailRange));
    window.requestAnimationFrame(drawMetricHistory);
  }

  async function loadMetricHistory() {
    if (!state.detailMetric) return;
    setText("#metric-detail-status", "Loading recorded history...");
    try {
      const response = await fetch(`/api/watch/history?metric=${encodeURIComponent(state.detailMetric)}&range=${encodeURIComponent(state.detailRange)}`, { cache: "no-store" });
      if (!response.ok) throw new Error("Metric history request failed");
      renderMetricHistory(await response.json());
    } catch (error) {
      setText("#metric-detail-status", "History could not be loaded.");
      $("#metric-history-empty").hidden = false;
    }
  }

  function openMetricHistory(metric) {
    state.detailMetric = metric;
    state.detailRange = state.range;
    state.detailPayload = null;
    setText("#metric-detail-title", watchMetricLabels[metric] || metric);
    setText("#metric-detail-value", "--");
    setText("#metric-detail-age", "--");
    setText("#metric-detail-source", "--");
    $("#metric-history-body").innerHTML = '<tr><td colspan="4">Loading history...</td></tr>';
    const dialog = $("#metric-dialog");
    if (!dialog.open) dialog.showModal();
    loadMetricHistory();
  }

  function focusTimeline() {
    const panel = $("#timeline-panel");
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
    window.setTimeout(() => panel.focus({ preventScroll: true }), 350);
  }

  async function loadDashboard({ quiet = false } = {}) {
    if (state.loading) return;
    state.loading = true;
    try {
      const response = await fetch(`/api/dashboard?range=${encodeURIComponent(state.range)}`, { cache: "no-store" });
      if (!response.ok) throw new Error("Dashboard data request failed");
      render(await response.json());
    } catch (error) {
      if (!quiet) showMessage("Could not load the local health database.", true);
      setStatusClass($("#rail-status-dot"), "error");
      setText("#rail-status", "Dashboard unavailable");
    } finally {
      state.loading = false;
    }
  }

  function mergeLiveState(payload) {
    renderCurrent(payload);
    if (!state.payload) return;

    state.payload.current = payload.current;
    state.payload.watch.latest = {
      ...state.payload.watch.latest,
      ...payload.watch.latest,
    };
    state.payload.watch.heart_rate_sync = payload.watch.heart_rate_sync;

    const heart = payload.watch.latest?.["vitals.heart_rate"];
    const sample = heart?.payload?.samples?.at(-1);
    if (!sample?.time || sample.value === null || sample.value === undefined) return;

    const points = state.payload.watch.heart_rate_samples || [];
    if (!points.some((point) => point.time === sample.time)) {
      points.push({
        time: sample.time,
        value: sample.value,
        attribution: heart.attribution?.state,
      });
      if (points.length > 1500) points.splice(0, points.length - 1500);
      state.payload.watch.heart_rate_samples = points;
      drawChart();
    }
  }

  async function loadLive({ quiet = false } = {}) {
    if (state.liveLoading) return;
    state.liveLoading = true;
    try {
      const response = await fetch("/api/live", { cache: "no-store" });
      if (!response.ok) throw new Error("Live data request failed");
      mergeLiveState(await response.json());
    } catch (error) {
      if (!quiet) showMessage("Live sensor refresh is temporarily unavailable.", true);
    } finally {
      state.liveLoading = false;
    }
  }

  async function refreshData() {
    const button = $("#refresh-button");
    button.disabled = true;
    button.classList.add("is-spinning");
    try {
      const response = await fetch("/api/refresh", {
        method: "POST",
        headers: { "X-Personal-State-Token": token, "Content-Type": "application/json" },
        body: "{}",
      });
      const result = await response.json();
      if (!response.ok || result.error) throw new Error("Refresh failed");
      await loadDashboard();
      showMessage(result.status === "skipped" ? "The background collector is already current." : "LibreLinkUp data refreshed.");
    } catch (error) {
      showMessage("LibreLinkUp refresh failed. Stored history is still available.", true);
    } finally {
      button.disabled = false;
      button.classList.remove("is-spinning");
    }
  }

  function exportCsv(source) {
    const watchExport = source === "watch";
    const link = document.createElement("a");
    link.href = `${watchExport ? "/api/watch/export.csv" : "/api/export.csv"}?range=${encodeURIComponent(state.range)}`;
    link.download = watchExport ? `personal-state-watch-${state.range}.csv` : `personal-state-glucose-${state.range}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  async function copyForCodex() {
    const question = $("#question-input").value.trim();
    const hours = rangeHours[state.range];
    const prompt = [
      "Use the Personal State MCP to answer this question about my available glucose and watch data:",
      question || "What stands out in the selected period?",
      "",
      `Selected range: ${rangeLabels[state.range]}.`,
      `Call health.current_state(), health.context(), and health.glucose_recent(hours=${hours}, limit=5000).`,
      "Call health.watch() and use health.watch_recent() only for a specific recorded metric when history is needed.",
      "State source, attribution, measurement time, received or ingest time, freshness, and important data gaps.",
      "Use the configured 80 mg/dL threshold only as decision-support context.",
      "Samsung/watch synchronization status is unknown. Do not treat missing data as normal.",
      "Do not diagnose causality or provide treatment, dosing, food, driving, or exercise instructions.",
    ].join("\n");
    try {
      await navigator.clipboard.writeText(prompt);
      setText("#copy-status", "Copied. Paste into this Codex task.");
    } catch (error) {
      const textarea = $("#question-input");
      textarea.value = prompt;
      textarea.select();
      setText("#copy-status", "Prompt selected. Copy it, then return to Codex.");
    }
  }

  function handleChartMove(event) {
    if (!state.chartPoints.length) return;
    const canvas = $("#glucose-chart");
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const nearest = state.chartPoints.reduce((best, candidate) => Math.abs(candidate.x - x) < Math.abs(best.x - x) ? candidate : best);
    const nearestInSeries = (series) => series.length
      ? series.reduce((best, candidate) => Math.abs(candidate.time - nearest.time) < Math.abs(best.time - nearest.time) ? candidate : best)
      : null;
    const nearbyGlucose = nearestInSeries(state.chartSeries.glucose);
    const nearbyHeart = nearestInSeries(state.chartSeries.heart);
    const tolerance = 10 * 60 * 1000;
    const showGlucose = nearbyGlucose && Math.abs(nearbyGlucose.time - nearest.time) <= tolerance;
    const showHeart = nearbyHeart && Math.abs(nearbyHeart.time - nearest.time) <= tolerance;
    const tooltip = $("#chart-tooltip");
    tooltip.innerHTML = `
      <span class="tooltip-time">${escapeHtml(formatDate(new Date(nearest.time).toISOString()))}</span>
      ${showGlucose ? `<span class="tooltip-row glucose"><b>Glucose</b><strong>${escapeHtml(nearbyGlucose.point.value_mg_dl)} mg/dL</strong></span>` : '<span class="tooltip-row muted"><b>Glucose</b><span>No nearby reading</span></span>'}
      ${showHeart ? `<span class="tooltip-row heart"><b>Heart rate</b><strong>${escapeHtml(nearbyHeart.point.value)} bpm</strong></span>` : '<span class="tooltip-row muted"><b>Heart rate</b><span>No nearby sample</span></span>'}
    `;
    tooltip.hidden = false;
    const left = Math.min(rect.width - 224, Math.max(8, nearest.x + 12));
    const top = Math.min(rect.height - 118, Math.max(8, nearest.y - 42));
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  }

  $$("#range-control button").forEach((button) => button.addEventListener("click", async () => {
    state.range = button.dataset.range;
    localStorage.setItem("personal-state-range", state.range);
    localStorage.setItem("personal-state-range-version", rangePreferenceVersion);
    await loadDashboard();
  }));
  $("#refresh-button").addEventListener("click", refreshData);
  $("#print-button").addEventListener("click", () => window.print());
  $("#table-export-button").addEventListener("click", () => exportCsv("glucose"));
  $("#watch-export-button").addEventListener("click", () => exportCsv("watch"));
  $("#ask-button").addEventListener("click", () => {
    setText("#copy-status", "");
    $("#ask-dialog").showModal();
    window.setTimeout(() => $("#question-input").focus(), 50);
  });
  $("#copy-prompt-button").addEventListener("click", copyForCodex);
  $("#glucose-chart").addEventListener("mousemove", handleChartMove);
  $("#glucose-chart").addEventListener("mouseleave", () => { $("#chart-tooltip").hidden = true; });
  $("#watch-latest-grid").addEventListener("click", (event) => {
    const card = event.target.closest("[data-metric]");
    if (card) openMetricHistory(card.dataset.metric);
  });
  $$("[data-timeline-focus]").forEach((card) => {
    card.addEventListener("click", focusTimeline);
    card.addEventListener("keydown", (event) => {
      if (["Enter", " "].includes(event.key)) { event.preventDefault(); focusTimeline(); }
    });
  });
  $$("#metric-range-control button").forEach((button) => button.addEventListener("click", () => {
    state.detailRange = button.dataset.range;
    loadMetricHistory();
  }));
  $("#metric-dialog-close").addEventListener("click", () => $("#metric-dialog").close());

  const workspaceNavLinks = $$(".workspace-nav a");
  workspaceNavLinks.forEach((link) => link.addEventListener("click", () => {
    const detailsId = link.dataset.openDetails;
    if (detailsId) {
      const details = document.getElementById(detailsId);
      if (details) details.open = true;
    }
    workspaceNavLinks.forEach((candidate) => candidate.classList.toggle("is-active", candidate === link));
  }));

  const navTargets = [
    ["overview", "#overview"],
    ["signals", "#signals"],
    ["timeline-panel", "#timeline-panel"],
    ["records", "#records"],
  ];
  const navObserver = new IntersectionObserver((entries) => {
    const visible = entries
      .filter((entry) => entry.isIntersecting)
      .sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0];
    if (!visible) return;
    const href = navTargets.find(([id]) => id === visible.target.id)?.[1];
    workspaceNavLinks.forEach((link) => link.classList.toggle("is-active", link.getAttribute("href") === href));
  }, { rootMargin: "-15% 0px -68% 0px", threshold: [0, .15, .4] });
  navTargets.forEach(([id]) => {
    const target = document.getElementById(id);
    if (target) navObserver.observe(target);
  });

  const resizeObserver = new ResizeObserver(() => window.requestAnimationFrame(drawChart));
  resizeObserver.observe($("#chart-wrap"));
  window.setInterval(() => {
    if (document.visibilityState === "visible") loadLive({ quiet: true });
  }, 1_000);
  window.setInterval(() => {
    if (document.visibilityState === "visible") loadDashboard({ quiet: true });
  }, 60_000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      loadLive({ quiet: true });
      loadDashboard({ quiet: true });
    }
  });

  if (!rangeLabels[state.range]) state.range = "24h";
  localStorage.setItem("personal-state-range", state.range);
  localStorage.setItem("personal-state-range-version", rangePreferenceVersion);
  loadDashboard();
})();
