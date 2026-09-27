package ai.clinicianassist.personalstate

import android.app.Activity
import android.content.Context
import android.util.Base64
import com.samsung.android.sdk.health.data.HealthDataService
import com.samsung.android.sdk.health.data.HealthDataStore
import com.samsung.android.sdk.health.data.data.Change
import com.samsung.android.sdk.health.data.data.ChangeType
import com.samsung.android.sdk.health.data.data.HealthDataPoint
import com.samsung.android.sdk.health.data.data.entries.OxygenSaturation
import com.samsung.android.sdk.health.data.data.entries.SkinTemperature
import com.samsung.android.sdk.health.data.permission.AccessType
import com.samsung.android.sdk.health.data.permission.Permission
import com.samsung.android.sdk.health.data.request.DataType
import com.samsung.android.sdk.health.data.request.DataTypes
import com.samsung.android.sdk.health.data.request.InstantTimeFilter
import com.samsung.android.sdk.health.data.request.IdFilter
import com.samsung.android.sdk.health.data.request.LocalDateFilter
import com.samsung.android.sdk.health.data.request.LocalTimeFilter
import com.samsung.android.sdk.health.data.request.LocalTimeGroup
import com.samsung.android.sdk.health.data.request.LocalTimeGroupUnit
import com.samsung.android.sdk.health.data.request.Ordering
import org.json.JSONArray
import org.json.JSONObject
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.temporal.ChronoUnit
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import kotlin.math.roundToInt

class SamsungHealthDataSdkAdapter(
    context: Context,
    private val pairingStore: PairingStore,
) : SamsungHealthDataAdapter {
    private val healthStore: HealthDataStore = HealthDataService.getStore(context)
    private val associatedOxygenUids = mutableSetOf<String>()
    private val associatedTemperatureUids = mutableSetOf<String>()
    override val installed = true

    override suspend fun requestPermissions(activity: Activity): SamsungPermissionResult {
        val granted = healthStore.requestPermissions(PERMISSIONS, activity)
        return SamsungPermissionResult(
            granted = granted.size,
            requested = PERMISSIONS.size,
            message = if (granted.containsAll(PERMISSIONS)) {
                "Samsung Health access is allowed for all supported data."
            } else {
                "Samsung Health access allowed for ${granted.size} of ${PERMISSIONS.size} data categories."
            },
        )
    }

    override suspend fun collect(): SamsungAdapterResult {
        val pairing = pairingStore.pairing()
            ?: error("Pair this phone before reading Samsung Health.")
        val identityKey = pairing.recordIdentityKey
            ?: error("Pairing must include a record identity key for Samsung Health Data.")
        val hasher = RecordHasher(identityKey)
        associatedOxygenUids.clear()
        associatedTemperatureUids.clear()
        val granted = healthStore.getGrantedPermissions(PERMISSIONS)
        val now = Instant.now()
        val changes = mutableListOf<JSONObject>()
        val availability = mutableListOf<SamsungAdapterAvailability>()
        val checkpointUpdates = mutableMapOf<String, String>()
        val sleepStart = windowStart(CHECKPOINT_SLEEP, now)

        collectMetric(
            "sleep.summary", CHECKPOINT_SLEEP, PERMISSION_SLEEP, granted,
            sleepStart, now, changes, availability, checkpointUpdates,
        ) {
            if (hasCheckpoint(CHECKPOINT_SLEEP)) {
                readSleepChanges(sleepStart, now, hasher)
            } else {
                readSleep(
                    LocalDateTime.ofInstant(sleepStart, ZoneId.systemDefault()),
                    LocalDateTime.ofInstant(now, ZoneId.systemDefault()),
                    hasher,
                    now,
                )
            }
        }
        val oxygenStart = windowStart(CHECKPOINT_OXYGEN, now)
        collectMetric(
            "vitals.oxygen_saturation_series", CHECKPOINT_OXYGEN, PERMISSION_OXYGEN, granted,
            oxygenStart, now, changes, availability, checkpointUpdates,
        ) {
            if (hasCheckpoint(CHECKPOINT_OXYGEN)) readOxygenChanges(oxygenStart, now, hasher)
            else readOxygen(oxygenStart, now, hasher)
        }
        val temperatureStart = windowStart(CHECKPOINT_TEMPERATURE, now)
        collectMetric(
            "vitals.skin_temperature", CHECKPOINT_TEMPERATURE, PERMISSION_SKIN_TEMPERATURE, granted,
            temperatureStart, now, changes, availability, checkpointUpdates,
        ) {
            if (hasCheckpoint(CHECKPOINT_TEMPERATURE)) readTemperatureChanges(temperatureStart, now, hasher)
            else readSkinTemperature(temperatureStart, now, hasher)
        }
        val energyStart = windowStart(CHECKPOINT_ENERGY, now)
        collectMetric(
            "wellness.energy_score", CHECKPOINT_ENERGY, PERMISSION_ENERGY_SCORE, granted,
            energyStart, now, changes, availability, checkpointUpdates,
        ) {
            if (hasCheckpoint(CHECKPOINT_ENERGY)) readEnergyChanges(energyStart, now, hasher)
            else readEnergyScores(
                LocalDateTime.ofInstant(energyStart, ZoneId.systemDefault()).toLocalDate(),
                LocalDate.now().plusDays(1),
                hasher,
            )
        }
        val rhythmStart = windowStart(CHECKPOINT_RHYTHM, now)
        collectMetric(
            "cardiac.irregular_rhythm_notification", CHECKPOINT_RHYTHM, PERMISSION_RHYTHM, granted,
            rhythmStart, now, changes, availability, checkpointUpdates,
        ) {
            if (hasCheckpoint(CHECKPOINT_RHYTHM)) readRhythmChanges(rhythmStart, now, hasher)
            else readRhythm(rhythmStart, now, hasher)
        }
        val apneaStart = windowStart(CHECKPOINT_APNEA, now)
        collectMetric(
            "sleep.apnea_detected_sign", CHECKPOINT_APNEA, PERMISSION_APNEA, granted,
            apneaStart, now, changes, availability, checkpointUpdates,
        ) {
            if (hasCheckpoint(CHECKPOINT_APNEA)) readApneaChanges(apneaStart, now, hasher)
            else readSleepApnea(apneaStart, now, hasher)
        }
        val floorsStart = windowStart(CHECKPOINT_FLOORS, now)
        collectMetric(
            "activity.floors", CHECKPOINT_FLOORS, PERMISSION_FLOORS, granted,
            floorsStart, now, changes, availability, checkpointUpdates,
        ) {
            if (hasCheckpoint(CHECKPOINT_FLOORS)) readFloorChanges(floorsStart, now, hasher)
            else readFloors(floorsStart, now, hasher)
        }
        val activityStart = FULL_HISTORY_START
        collectMetric(
            "activity.active_time", "", PERMISSION_ACTIVITY, granted,
            activityStart, now, changes, availability, checkpointUpdates,
        ) {
            readActiveTime(
                LocalDateTime.ofInstant(activityStart, ZoneId.systemDefault()),
                LocalDateTime.ofInstant(now, ZoneId.systemDefault()),
                hasher,
                now,
            )
        }

        val sleepAvailable = changes.any { change ->
            change.getJSONArray("observations").toJsonObjects().any {
                it.getString("metric") == "sleep.samsung_session"
            }
        }
        val scoreAvailable = changes.any { change ->
            change.getJSONArray("observations").toJsonObjects().any {
                it.getString("metric") == "sleep.score"
            }
        }
        val sleepParent = availability.first { it.metric == "sleep.summary" }
        availability += derivedSleepAvailability("sleep.samsung_session", sleepAvailable, sleepParent)
        availability += derivedSleepAvailability("sleep.score", scoreAvailable, sleepParent)
        AVAILABILITY_ONLY.forEach { metric ->
            availability += SamsungAdapterAvailability(
                metric,
                "not_exposed_by_provider",
                "Samsung Health Data SDK 1.1.0 does not expose observation records for this category.",
                FULL_HISTORY_START,
                now,
            )
        }
        return SamsungAdapterResult(
            ADAPTER,
            changes,
            availability.distinctBy { it.metric },
            checkpointUpdates,
        )
    }

    private suspend fun collectMetric(
        metric: String,
        checkpointKey: String,
        permission: Permission,
        granted: Set<Permission>,
        windowStart: Instant,
        windowEnd: Instant,
        changes: MutableList<JSONObject>,
        availability: MutableList<SamsungAdapterAvailability>,
        checkpointUpdates: MutableMap<String, String>,
        reader: suspend () -> List<JSONObject>,
    ) {
        val isInitialBackfill = checkpointKey.isNotBlank() && !hasCheckpoint(checkpointKey)
        if (permission !in granted) {
            availability += SamsungAdapterAvailability(
                metric,
                "permission_required",
                "Samsung Health read permission has not been granted for this category.",
                windowStart,
                windowEnd,
            )
            return
        }
        runCatching { reader() }
            .onSuccess { read ->
                changes += read
                if (checkpointKey.isNotBlank()) checkpointUpdates[checkpointKey] = windowEnd.toString()
                availability += metricAvailability(
                    metric,
                    if (read.isEmpty()) "no_observation" else "available",
                    windowStart,
                    windowEnd,
                    backfillLimited = false,
                    reconciling = checkpointKey.isNotBlank() && !isInitialBackfill,
                )
            }
            .onFailure { error ->
                availability += SamsungAdapterAvailability(
                    metric,
                    "companion_read_failed",
                    "Samsung Health read failed: ${error::class.simpleName ?: "error"}.",
                    windowStart,
                    windowEnd,
                    truncated = error is DataLimitExceeded,
                    interrupted = true,
                )
            }
    }

    private suspend fun readSleep(
        start: LocalDateTime,
        end: LocalDateTime,
        hasher: RecordHasher,
        observedAt: Instant,
    ): List<JSONObject> {
        val points = mutableListOf<HealthDataPoint>()
        var pageToken: String? = null
        do {
            val builder = DataTypes.SLEEP.readDataRequestBuilder
                .setLocalTimeFilter(LocalTimeFilter.of(start, end))
                .setOrdering(Ordering.ASC)
                .setPageSize(PAGE_SIZE)
            pageToken?.let(builder::setPageToken)
            val response = healthStore.readData(builder.build())
            points += response.dataList
            pageToken = response.pageToken
        } while (!pageToken.isNullOrBlank())

        val results = mutableListOf<JSONObject>()
        for (point in points) {
            sleepSourceChange(point, hasher, observedAt)?.let(results::add)
        }
        return results
    }

    private suspend fun sleepSourceChange(
        point: HealthDataPoint,
        hasher: RecordHasher,
        observedAt: Instant,
        changedAt: Instant = point.updateTime ?: observedAt,
    ): JSONObject? {
        val sourcePackage = sourcePackage(point) ?: return null
        val associationHash = hasher.source("SleepType", point.uid)
        val observations = mutableListOf<JSONObject>()
        val sessions = point.getValueOrDefault(DataType.SleepType.SESSIONS, emptyList())
            .sortedBy { it.startTime }
        if (sessions.size + 4 > MAX_SOURCE_MEMBERS) {
            throw DataLimitExceeded("A Samsung sleep family exceeds the source-change member limit.")
        }
        val sessionHashes = mutableListOf<String>()
        sessions.forEach { session ->
            val recordHash = hasher.child(
                associationHash,
                "session:${session.startTime}:${session.endTime}",
            )
            sessionHashes += recordHash
            val stages = session.stages.orEmpty().sortedBy { it.startTime }
            if (stages.size > MAX_SERIES_ITEMS) {
                throw DataLimitExceeded("A Samsung sleep session exceeds the stage limit.")
            }
            observations += baseObservation(point, sourcePackage, recordHash, "sleep.samsung_session", "session", observedAt)
                .put("association_hash", associationHash)
                .put("start_at", session.startTime.toString())
                .put("end_at", session.endTime.toString())
                .put("start_zone_offset", offset(point))
                .put("end_zone_offset", offset(point))
                .put("payload", JSONObject()
                    .put("duration_minutes", session.duration.toMinutes())
                    .put("stages", JSONArray(stages.map { stage ->
                        JSONObject()
                            .put("start_at", stage.startTime.toString())
                            .put("end_at", stage.endTime.toString())
                            .put("stage", sleepStage(stage.stage))
                    })))
        }
        val duration = point.getValueOrDefault(DataType.SleepType.DURATION, Duration.ZERO)
        observations += baseObservation(
            point,
            sourcePackage,
            hasher.child(associationHash, "summary"),
            "sleep.summary",
            "session",
            observedAt,
        )
            .put("association_hash", associationHash)
            .put("start_at", point.startTime.toString())
            .put("end_at", point.endTime.toString())
            .put("start_zone_offset", offset(point))
            .put("end_zone_offset", offset(point))
            .put("payload", JSONObject()
                .put("duration_minutes", duration.toMinutes())
                .put("session_record_hashes", JSONArray(sessionHashes)))
        point.getValue(DataType.SleepType.SLEEP_SCORE)?.let { score ->
            observations += baseObservation(
                point,
                sourcePackage,
                hasher.child(associationHash, "score"),
                "sleep.score",
                "point",
                observedAt,
            )
                .put("association_hash", associationHash)
                .put("measured_at", point.endTime.toString())
                .put("zone_offset", offset(point))
                .put("payload", JSONObject().put("value", score).put("unit", "score"))
        }
        observations += readSleepAssociations(point, associationHash, hasher, observedAt)
        if (observations.size > MAX_SOURCE_MEMBERS) {
            throw DataLimitExceeded("A Samsung sleep family exceeds the source-change member limit.")
        }
        return sourceChange("SleepType", changedAt, observations, associationHash)
    }

    private suspend fun readSleepAssociations(
        sleep: HealthDataPoint,
        associationHash: String,
        hasher: RecordHasher,
        observedAt: Instant,
    ): List<JSONObject> {
        val request = DataTypes.SLEEP.associatedReadRequestBuilder
            .setIdFilter(IdFilter.fromDataUid(sleep.uid))
            .addAssociatedDataType(DataType.SleepType.Associates.BLOOD_OXYGEN)
            .addAssociatedDataType(DataType.SleepType.Associates.SKIN_TEMPERATURE)
            .build()
        val associated = healthStore.readAssociatedData(request).dataList
            .firstOrNull { it.uid == sleep.uid }
            ?: return emptyList()
        val sourcePackage = sourcePackage(sleep) ?: return emptyList()
        val results = mutableListOf<JSONObject>()
        associated.getDataPointOf(DataTypes.BLOOD_OXYGEN).orEmpty().forEach { point ->
            associatedOxygenUids += point.uid
            oxygenObservation(point, sourcePackage, hasher.child(associationHash, "oxygen:${point.uid}"), observedAt)
                ?.let { results += it.put("association_hash", associationHash) }
        }
        associated.getDataPointOf(DataTypes.SKIN_TEMPERATURE).orEmpty().forEach { point ->
            associatedTemperatureUids += point.uid
            temperatureObservation(point, sourcePackage, hasher.child(associationHash, "temperature:${point.uid}"), observedAt)
                ?.let { results += it.put("association_hash", associationHash) }
        }
        return results
    }

    private suspend fun readOxygen(start: Instant, end: Instant, hasher: RecordHasher): List<JSONObject> =
        readDualTime(DataTypes.BLOOD_OXYGEN, start, end).mapNotNull { oxygenSourceChange(it, hasher) }

    private fun oxygenSourceChange(
        point: HealthDataPoint,
        hasher: RecordHasher,
        changedAt: Instant = point.updateTime ?: Instant.now(),
    ): JSONObject? {
        if (point.uid in associatedOxygenUids) return null
        val sourcePackage = sourcePackage(point) ?: return null
        val observation = oxygenObservation(
            point,
            sourcePackage,
            hasher.source("BloodOxygenType", point.uid),
            Instant.now(),
        ) ?: return null
        return sourceChange("BloodOxygenType", changedAt, listOf(observation))
    }

    private fun oxygenObservation(
        point: HealthDataPoint,
        sourcePackage: String,
        recordHash: String,
        observedAt: Instant,
    ): JSONObject? {
            val entries = point.getValueOrDefault(DataType.BloodOxygenType.SERIES_DATA, emptyList())
                .sortedBy { it.startTime }
            if (entries.size > MAX_SERIES_ITEMS) {
                throw DataLimitExceeded("A Samsung oxygen series exceeds the sample limit.")
            }
            val samples = oxygenSamples(point, entries)
            if (samples.isEmpty()) return null
            val values = samples.map { it.second }
            return baseObservation(
                point,
                sourcePackage,
                recordHash,
                "vitals.oxygen_saturation_series",
                "series",
                observedAt,
            )
                .put("start_at", samples.first().first.toString())
                .put("end_at", samples.last().first.toString())
                .put("start_zone_offset", offset(point))
                .put("end_zone_offset", offset(point))
                .put("payload", seriesPayload(samples, "%", values.min(), values.max()))
    }

    private suspend fun readSkinTemperature(start: Instant, end: Instant, hasher: RecordHasher): List<JSONObject> =
        readDualTime(DataTypes.SKIN_TEMPERATURE, start, end).mapNotNull { temperatureSourceChange(it, hasher) }

    private fun temperatureSourceChange(
        point: HealthDataPoint,
        hasher: RecordHasher,
        changedAt: Instant = point.updateTime ?: Instant.now(),
    ): JSONObject? {
        if (point.uid in associatedTemperatureUids) return null
        val sourcePackage = sourcePackage(point) ?: return null
        val observation = temperatureObservation(
            point,
            sourcePackage,
            hasher.source("SkinTemperatureType", point.uid),
            Instant.now(),
        ) ?: return null
        return sourceChange("SkinTemperatureType", changedAt, listOf(observation))
    }

    private fun temperatureObservation(
        point: HealthDataPoint,
        sourcePackage: String,
        recordHash: String,
        observedAt: Instant,
    ): JSONObject? {
            val entries = point.getValueOrDefault(DataType.SkinTemperatureType.SERIES_DATA, emptyList())
                .sortedBy { it.startTime }
            if (entries.size > MAX_SERIES_ITEMS) {
                throw DataLimitExceeded("A Samsung temperature series exceeds the sample limit.")
            }
            val samples = skinSamples(point, entries)
            if (samples.isEmpty()) return null
            val values = samples.map { it.second }
            return baseObservation(
                point,
                sourcePackage,
                recordHash,
                "vitals.skin_temperature",
                "series",
                observedAt,
            )
                .put("start_at", samples.first().first.toString())
                .put("end_at", samples.last().first.toString())
                .put("start_zone_offset", offset(point))
                .put("end_zone_offset", offset(point))
                .put("payload", seriesPayload(samples, "Cel", values.min(), values.max()))
    }

    private suspend fun readEnergyScores(start: LocalDate, end: LocalDate, hasher: RecordHasher): List<JSONObject> =
        readLocalDate(DataTypes.ENERGY_SCORE, start, end).mapNotNull { energySourceChange(it, hasher) }

    private fun energySourceChange(
        point: HealthDataPoint,
        hasher: RecordHasher,
        changedAt: Instant = point.updateTime ?: Instant.now(),
    ): JSONObject? {
        val sourcePackage = sourcePackage(point) ?: return null
        val score = point.getValue(DataType.EnergyScoreType.ENERGY_SCORE) ?: return null
        val localOffset = point.zoneOffset ?: ZoneId.systemDefault().rules.getOffset(point.startTime)
        val localDate = point.startTime.atOffset(localOffset).toLocalDate()
        val observation = baseObservation(
            point,
            sourcePackage,
            hasher.source("EnergyScoreType", point.uid),
            "wellness.energy_score",
            "daily",
            Instant.now(),
        )
            .put("measured_at", point.startTime.toString())
            .put("local_date", localDate.toString())
            .put("zone_offset", offset(point))
            .put("payload", JSONObject().put("value", score).put("unit", "score"))
        return sourceChange("EnergyScoreType", changedAt, listOf(observation))
    }

    private suspend fun readRhythm(start: Instant, end: Instant, hasher: RecordHasher): List<JSONObject> =
        readDualTime(DataTypes.IRREGULAR_HEART_RHYTHM_NOTIFICATION, start, end).mapNotNull { rhythmSourceChange(it, hasher) }

    private fun rhythmSourceChange(
        point: HealthDataPoint,
        hasher: RecordHasher,
        changedAt: Instant = point.updateTime ?: Instant.now(),
    ): JSONObject? {
        val sourcePackage = sourcePackage(point) ?: return null
        val status = point.getValue(DataType.IrregularHeartRhythmNotificationType.STATUS)
            ?.name?.lowercase() ?: "undefined"
        val observation = pointObservation(
            point, sourcePackage, hasher.source("IrregularHeartRhythmNotificationType", point.uid),
            "cardiac.irregular_rhythm_notification", JSONObject().put("status", status),
        )
        return sourceChange("IrregularHeartRhythmNotificationType", changedAt, listOf(observation))
    }

    private suspend fun readSleepApnea(start: Instant, end: Instant, hasher: RecordHasher): List<JSONObject> =
        readDualTime(DataTypes.SLEEP_APNEA, start, end).mapNotNull { apneaSourceChange(it, hasher) }

    private fun apneaSourceChange(
        point: HealthDataPoint,
        hasher: RecordHasher,
        changedAt: Instant = point.updateTime ?: Instant.now(),
    ): JSONObject? {
        val sourcePackage = sourcePackage(point) ?: return null
        val status = when (point.getValue(DataType.SleepApneaType.DETECTED_SIGN)?.name) {
            "DETECTED" -> "detected"
            "NOT_DETECTED" -> "not_detected"
            else -> "undefined"
        }
        val observation = pointObservation(
            point, sourcePackage, hasher.source("SleepApneaType", point.uid),
            "sleep.apnea_detected_sign", JSONObject().put("status", status),
        )
        return sourceChange("SleepApneaType", changedAt, listOf(observation))
    }

    private suspend fun readFloors(
        start: Instant,
        end: Instant,
        hasher: RecordHasher,
    ): List<JSONObject> = readDualTime(DataTypes.FLOORS_CLIMBED, start, end)
        .mapNotNull { floorSourceChange(it, hasher) }

    private fun floorSourceChange(
        point: HealthDataPoint,
        hasher: RecordHasher,
        changedAt: Instant = point.updateTime ?: Instant.now(),
    ): JSONObject? {
        val sourcePackage = sourcePackage(point) ?: return null
        val value = point.getValue(DataType.FloorsClimbedType.FLOOR)?.roundToInt() ?: return null
        val observation = baseObservation(
            point,
            sourcePackage,
            hasher.source("FloorsClimbedType", point.uid),
            "activity.floors",
            "aggregate",
            Instant.now(),
        )
            .put("start_at", point.startTime.toString())
            .put("end_at", point.endTime.toString())
            .put("start_zone_offset", offset(point))
            .put("end_zone_offset", offset(point))
            .put("payload", JSONObject().put("value", value).put("unit", "count"))
        return sourceChange("FloorsClimbedType", changedAt, listOf(observation))
    }

    private suspend fun readActiveTime(
        start: LocalDateTime,
        end: LocalDateTime,
        hasher: RecordHasher,
        observedAt: Instant,
    ): List<JSONObject> {
        val request = DataType.ActivitySummaryType.TOTAL_ACTIVE_TIME.requestBuilder
            .setLocalTimeFilterWithGroup(
                LocalTimeFilter.of(start, end),
                LocalTimeGroup.of(LocalTimeGroupUnit.DAILY, 1),
            )
            .setOrdering(Ordering.ASC)
            .build()
        return healthStore.aggregateData(request).dataList.mapNotNull { aggregate ->
            val value = aggregate.value ?: return@mapNotNull null
            val hash = hasher.source("ActivitySummaryType", "${aggregate.startTime}:${aggregate.endTime}")
            val observation = aggregateObservation(
                hash, "activity.active_time", aggregate.startTime, aggregate.endTime,
                value.toMinutes().toInt(), "min", observedAt,
            )
            sourceChange("ActivitySummaryType", observedAt, listOf(observation))
        }
    }

    private suspend fun readSleepChanges(
        start: Instant,
        end: Instant,
        hasher: RecordHasher,
    ): List<JSONObject> {
        val results = mutableListOf<JSONObject>()
        readChanged(DataTypes.SLEEP, start, end).forEach { change ->
            when (change.changeType) {
                ChangeType.UPSERT -> change.upsertDataPoint
                    ?.let { sleepSourceChange(it, hasher, Instant.now(), change.changeTime) }
                    ?.let(results::add)
                ChangeType.DELETE -> change.deleteDataUid?.let { uid ->
                    results += sleepDeleteChange(uid, change.changeTime, hasher)
                }
            }
        }
        return results
    }

    private suspend fun readOxygenChanges(start: Instant, end: Instant, hasher: RecordHasher) =
        readSimpleChanges(
            DataTypes.BLOOD_OXYGEN, "BloodOxygenType", "vitals.oxygen_saturation_series",
            start, end, hasher, ::oxygenSourceChange,
        )

    private suspend fun readTemperatureChanges(start: Instant, end: Instant, hasher: RecordHasher) =
        readSimpleChanges(
            DataTypes.SKIN_TEMPERATURE, "SkinTemperatureType", "vitals.skin_temperature",
            start, end, hasher, ::temperatureSourceChange,
        )

    private suspend fun readEnergyChanges(start: Instant, end: Instant, hasher: RecordHasher) =
        readSimpleChanges(
            DataTypes.ENERGY_SCORE, "EnergyScoreType", "wellness.energy_score",
            start, end, hasher, ::energySourceChange,
        )

    private suspend fun readRhythmChanges(start: Instant, end: Instant, hasher: RecordHasher) =
        readSimpleChanges(
            DataTypes.IRREGULAR_HEART_RHYTHM_NOTIFICATION,
            "IrregularHeartRhythmNotificationType",
            "cardiac.irregular_rhythm_notification",
            start, end, hasher, ::rhythmSourceChange,
        )

    private suspend fun readApneaChanges(start: Instant, end: Instant, hasher: RecordHasher) =
        readSimpleChanges(
            DataTypes.SLEEP_APNEA, "SleepApneaType", "sleep.apnea_detected_sign",
            start, end, hasher, ::apneaSourceChange,
        )

    private suspend fun readFloorChanges(start: Instant, end: Instant, hasher: RecordHasher) =
        readSimpleChanges(
            DataTypes.FLOORS_CLIMBED, "FloorsClimbedType", "activity.floors",
            start, end, hasher, ::floorSourceChange,
        )

    private suspend fun readSimpleChanges(
        type: DataType.ChangeReadable<HealthDataPoint>,
        sourceType: String,
        metric: String,
        start: Instant,
        end: Instant,
        hasher: RecordHasher,
        upsert: (HealthDataPoint, RecordHasher, Instant) -> JSONObject?,
    ): List<JSONObject> = readChanged(type, start, end).mapNotNull { change ->
        when (change.changeType) {
            ChangeType.UPSERT -> change.upsertDataPoint?.let { upsert(it, hasher, change.changeTime) }
            ChangeType.DELETE -> change.deleteDataUid?.let { uid ->
                deleteSourceChange(sourceType, metric, uid, change.changeTime, hasher)
            }
        }
    }

    private suspend fun readChanged(
        type: DataType.ChangeReadable<HealthDataPoint>,
        start: Instant,
        end: Instant,
    ): List<Change<HealthDataPoint>> {
        val results = mutableListOf<Change<HealthDataPoint>>()
        var token: String? = null
        do {
            val builder = type.changedDataRequestBuilder
                .setChangeTimeFilter(InstantTimeFilter.of(start, end))
                .setPageSize(PAGE_SIZE)
            token?.let(builder::setPageToken)
            val response = healthStore.readChanges(builder.build())
            results += response.dataList
            token = response.pageToken
        } while (!token.isNullOrBlank())
        return results
    }

    private fun sleepDeleteChange(
        uid: String,
        changedAt: Instant,
        hasher: RecordHasher,
    ): JSONObject {
        val associationHash = hasher.source("SleepType", uid)
        return JSONObject()
            .put("operation", "delete")
            .put("source_type", "SleepType")
            .put("changed_at", changedAt.toString())
            .put("adapter", ADAPTER)
            .put("observations", JSONArray())
            .put("deletions", JSONArray())
            .put("association_manifest", JSONObject()
                .put("adapter", ADAPTER)
                .put("association_hash", associationHash)
                .put("members", JSONArray()))
    }

    private fun deleteSourceChange(
        sourceType: String,
        metric: String,
        uid: String,
        changedAt: Instant,
        hasher: RecordHasher,
    ) = JSONObject()
        .put("operation", "delete")
        .put("source_type", sourceType)
        .put("changed_at", changedAt.toString())
        .put("adapter", ADAPTER)
        .put("observations", JSONArray())
        .put("deletions", JSONArray().put(JSONObject()
            .put("adapter", ADAPTER)
            .put("record_hash", hasher.source(sourceType, uid))
            .put("metric", metric)
            .put("source_package", SAMSUNG_HEALTH_PACKAGE)
            .put("upstream_last_modified_at", changedAt.toString())))

    private suspend fun readDualTime(
        type: DataType.Readable<HealthDataPoint, *>,
        start: Instant,
        end: Instant,
    ): List<HealthDataPoint> {
        val results = mutableListOf<HealthDataPoint>()
        var token: String? = null
        do {
            @Suppress("UNCHECKED_CAST")
            val builder = type.readDataRequestBuilder as com.samsung.android.sdk.health.data.request.ReadDataRequest.DualTimeBuilder<HealthDataPoint>
            builder.setInstantTimeFilter(InstantTimeFilter.of(start, end))
                .setOrdering(Ordering.ASC)
                .setPageSize(PAGE_SIZE)
            token?.let(builder::setPageToken)
            val response = healthStore.readData(builder.build())
            results += response.dataList
            token = response.pageToken
        } while (!token.isNullOrBlank())
        return results
    }

    private suspend fun readLocalDate(
        type: DataType.Readable<HealthDataPoint, *>,
        start: LocalDate,
        end: LocalDate,
    ): List<HealthDataPoint> {
        val results = mutableListOf<HealthDataPoint>()
        var token: String? = null
        do {
            @Suppress("UNCHECKED_CAST")
            val builder = type.readDataRequestBuilder as com.samsung.android.sdk.health.data.request.ReadDataRequest.LocalDateBuilder<HealthDataPoint>
            builder.setLocalDateFilter(LocalDateFilter.of(start, end))
                .setOrdering(Ordering.ASC)
                .setPageSize(PAGE_SIZE)
            token?.let(builder::setPageToken)
            val response = healthStore.readData(builder.build())
            results += response.dataList
            token = response.pageToken
        } while (!token.isNullOrBlank())
        return results
    }

    private fun oxygenSamples(
        point: HealthDataPoint,
        entries: List<OxygenSaturation>,
    ): List<Pair<Instant, Double>> = if (entries.isNotEmpty()) {
        entries.map { it.startTime to it.oxygenSaturation.toDouble() }
    } else {
        point.getValue(DataType.BloodOxygenType.OXYGEN_SATURATION)
            ?.let { listOf(point.startTime to it.toDouble()) }.orEmpty()
    }

    private fun skinSamples(
        point: HealthDataPoint,
        entries: List<SkinTemperature>,
    ): List<Pair<Instant, Double>> = if (entries.isNotEmpty()) {
        entries.map { it.startTime to it.skinTemperature.toDouble() }
    } else {
        point.getValue(DataType.SkinTemperatureType.SKIN_TEMPERATURE)
            ?.let { listOf(point.startTime to it.toDouble()) }.orEmpty()
    }

    private fun seriesPayload(
        samples: List<Pair<Instant, Double>>,
        unit: String,
        minimum: Double,
        maximum: Double,
    ) = JSONObject()
        .put("samples", JSONArray(samples.map { (time, value) ->
            JSONObject().put("time", time.toString()).put("value", value)
        }))
        .put("unit", unit)
        .put("minimum", minimum)
        .put("maximum", maximum)

    private fun pointObservation(
        point: HealthDataPoint,
        sourcePackage: String,
        hash: String,
        metric: String,
        payload: JSONObject,
    ) = baseObservation(point, sourcePackage, hash, metric, "point", Instant.now())
        .put("measured_at", point.startTime.toString())
        .put("zone_offset", offset(point))
        .put("payload", payload)

    private fun aggregateObservation(
        hash: String,
        metric: String,
        start: Instant,
        end: Instant,
        value: Int,
        unit: String,
        observedAt: Instant,
    ) = JSONObject()
        .put("adapter", ADAPTER)
        .put("record_hash", hash)
        .put("metric", metric)
        .put("record_kind", "aggregate")
        .put("start_at", start.toString())
        .put("end_at", end.toString())
        .put("start_zone_offset", ZoneId.systemDefault().rules.getOffset(start).id)
        .put("end_zone_offset", ZoneId.systemDefault().rules.getOffset(end).id)
        .put("observed_by_companion_at", observedAt.toString())
        .put("upstream_last_modified_at", observedAt.toString())
        .put("source_package", SAMSUNG_HEALTH_PACKAGE)
        .put("recording_method", "automatic")
        .put("attribution", unattributed())
        .put("payload", JSONObject().put("value", value).put("unit", unit))

    private fun baseObservation(
        point: HealthDataPoint,
        sourcePackage: String,
        hash: String,
        metric: String,
        kind: String,
        observedAt: Instant,
    ) = JSONObject()
        .put("adapter", ADAPTER)
        .put("record_hash", hash)
        .put("metric", metric)
        .put("record_kind", kind)
        .put("observed_by_companion_at", observedAt.toString())
        .put("upstream_last_modified_at", (point.updateTime ?: observedAt).toString())
        .put("source_package", sourcePackage)
        .put("recording_method", "automatic")
        .put("attribution", unattributed())

    private fun sourceChange(
        sourceType: String,
        changedAt: Instant,
        observations: List<JSONObject>,
        associationHash: String? = null,
    ) = JSONObject()
        .put("operation", "upsert")
        .put("source_type", sourceType)
        .put("changed_at", changedAt.toString())
        .put("adapter", ADAPTER)
        .put("observations", JSONArray(observations))
        .put("deletions", JSONArray())
        .also { change ->
            associationHash?.let { hash ->
                change.put("association_manifest", JSONObject()
                    .put("adapter", ADAPTER)
                    .put("association_hash", hash)
                    .put("members", JSONArray(observations.map { observation ->
                        JSONObject()
                            .put("metric", observation.getString("metric"))
                            .put("record_hash", observation.getString("record_hash"))
                    })))
            }
        }

    private fun sourcePackage(point: HealthDataPoint): String? = point.dataSource?.appId
        .takeIf { it == SAMSUNG_HEALTH_PACKAGE || it == SAMSUNG_HEALTH_PACKAGE_ALTERNATE }

    private fun offset(point: HealthDataPoint): String =
        (point.zoneOffset ?: ZoneId.systemDefault().rules.getOffset(point.startTime)).id

    private fun unattributed() = JSONObject()
        .put("state", "samsung_health_unattributed")
        .put("evidence", "Samsung Health Data SDK record; source-device identity was deliberately not uploaded.")

    private fun metricAvailability(
        metric: String,
        state: String,
        windowStart: Instant,
        windowEnd: Instant,
        backfillLimited: Boolean = false,
        reconciling: Boolean = false,
    ) = SamsungAdapterAvailability(
        metric,
        state,
        if (state == "available") {
            "Samsung Health Data SDK returned records in the checked window."
        } else {
            "Samsung Health Data SDK returned no records in the checked window."
        },
        windowStart,
        windowEnd,
        backfillLimited = backfillLimited,
        reconciling = reconciling,
    )

    private fun derivedSleepAvailability(
        metric: String,
        available: Boolean,
        parent: SamsungAdapterAvailability,
    ): SamsungAdapterAvailability = if (parent.state in setOf("available", "no_observation")) {
        metricAvailability(
            metric,
            if (available) "available" else "no_observation",
            parent.windowStart!!,
            parent.windowEnd!!,
            parent.backfillLimited,
            parent.reconciling,
        )
    } else {
        parent.copy(metric = metric)
    }

    private fun windowStart(checkpointKey: String, now: Instant): Instant {
        val prior = pairingStore.checkpoint(checkpointKey)
            ?.let { runCatching { Instant.parse(it) }.getOrNull() }
        return prior?.minus(CHANGE_OVERLAP_MINUTES, ChronoUnit.MINUTES)
            ?: FULL_HISTORY_START
    }

    private fun hasCheckpoint(checkpointKey: String): Boolean = pairingStore.checkpoint(checkpointKey)
        ?.let { runCatching { Instant.parse(it) }.isSuccess }
        ?: false

    private fun sleepStage(stage: DataType.SleepType.StageType): String = when (stage) {
        DataType.SleepType.StageType.AWAKE -> "awake"
        DataType.SleepType.StageType.LIGHT -> "light"
        DataType.SleepType.StageType.DEEP -> "deep"
        DataType.SleepType.StageType.REM -> "rem"
        DataType.SleepType.StageType.UNDEFINED -> "unknown"
    }

    private fun JSONArray.toJsonObjects(): Sequence<JSONObject> = sequence {
        for (index in 0 until length()) yield(getJSONObject(index))
    }

    private class RecordHasher(encodedKey: String) {
        private val key = Base64.decode(encodedKey, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)

        fun source(sourceType: String, uid: String): String = hash("samsung-source\u0000$sourceType\u0000$uid")

        fun child(associationHash: String, canonical: String): String =
            hash("sleep-child\u0000$associationHash\u0000$canonical")

        private fun hash(value: String): String {
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(key, "HmacSHA256"))
            return mac.doFinal(value.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
        }
    }

    private class DataLimitExceeded(message: String) : IllegalStateException(message)

    private companion object {
        val FULL_HISTORY_START: Instant = Instant.parse("2000-01-01T00:00:00Z")
        const val CHANGE_OVERLAP_MINUTES = 5L
        const val PAGE_SIZE = 500
        const val MAX_SERIES_ITEMS = 2000
        const val MAX_SOURCE_MEMBERS = 500
        const val SAMSUNG_HEALTH_PACKAGE = "com.sec.android.app.shealth"
        const val SAMSUNG_HEALTH_PACKAGE_ALTERNATE = "com.samsung.android.shealth"
        const val CHECKPOINT_SLEEP = "samsung:SleepType"
        const val CHECKPOINT_OXYGEN = "samsung:BloodOxygenType"
        const val CHECKPOINT_TEMPERATURE = "samsung:SkinTemperatureType"
        const val CHECKPOINT_ENERGY = "samsung:EnergyScoreType"
        const val CHECKPOINT_RHYTHM = "samsung:IrregularHeartRhythmNotificationType"
        const val CHECKPOINT_APNEA = "samsung:SleepApneaType"
        const val CHECKPOINT_FLOORS = "samsung:FloorsClimbedType"

        val ADAPTER = JSONObject()
            .put("id", "android_samsung_health_data")
            .put("version", "1.1.0")

        val PERMISSION_SLEEP = Permission.of(DataTypes.SLEEP, AccessType.READ)
        val PERMISSION_OXYGEN = Permission.of(DataTypes.BLOOD_OXYGEN, AccessType.READ)
        val PERMISSION_SKIN_TEMPERATURE = Permission.of(DataTypes.SKIN_TEMPERATURE, AccessType.READ)
        val PERMISSION_ENERGY_SCORE = Permission.of(DataTypes.ENERGY_SCORE, AccessType.READ)
        val PERMISSION_RHYTHM = Permission.of(DataTypes.IRREGULAR_HEART_RHYTHM_NOTIFICATION, AccessType.READ)
        val PERMISSION_APNEA = Permission.of(DataTypes.SLEEP_APNEA, AccessType.READ)
        val PERMISSION_FLOORS = Permission.of(DataTypes.FLOORS_CLIMBED, AccessType.READ)
        val PERMISSION_ACTIVITY = Permission.of(DataTypes.ACTIVITY_SUMMARY, AccessType.READ)
        val PERMISSIONS = setOf(
            PERMISSION_SLEEP,
            PERMISSION_OXYGEN,
            PERMISSION_SKIN_TEMPERATURE,
            PERMISSION_ENERGY_SCORE,
            PERMISSION_RHYTHM,
            PERMISSION_APNEA,
            PERMISSION_FLOORS,
            PERMISSION_ACTIVITY,
        )
        val AVAILABILITY_ONLY = listOf("sleep.snoring", "cardiac.ecg", "wellness.stress")
    }
}
