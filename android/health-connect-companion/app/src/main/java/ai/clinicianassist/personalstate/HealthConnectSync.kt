package ai.clinicianassist.personalstate

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.changes.DeletionChange
import androidx.health.connect.client.changes.UpsertionChange
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.BasalMetabolicRateRecord
import androidx.health.connect.client.records.BloodGlucoseRecord
import androidx.health.connect.client.records.BloodPressureRecord
import androidx.health.connect.client.records.BodyFatRecord
import androidx.health.connect.client.records.DistanceRecord
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.HeightRecord
import androidx.health.connect.client.records.NutritionRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.PowerRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.SpeedRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.TotalCaloriesBurnedRecord
import androidx.health.connect.client.records.Vo2MaxRecord
import androidx.health.connect.client.records.WeightRecord
import androidx.health.connect.client.request.AggregateRequest
import androidx.health.connect.client.request.ChangesTokenRequest
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import androidx.health.connect.client.records.metadata.DataOrigin
import androidx.health.connect.client.records.metadata.Metadata
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.ZoneId
import java.time.temporal.ChronoUnit
import kotlin.reflect.KClass

private data class TypeSpec<T : Record>(
    val metric: String,
    val type: KClass<T>,
)

class HealthConnectSync(
    private val context: Context,
    private val store: PairingStore = PairingStore(context),
    private val client: HealthConnectClient = HealthConnectClient.getOrCreate(context),
    private val uploader: UploadClient = UploadClient(),
) {
    private val specs: List<TypeSpec<out Record>> = listOf(
        TypeSpec("activity.steps", StepsRecord::class),
        TypeSpec("activity.exercise_calories", TotalCaloriesBurnedRecord::class),
        TypeSpec("activity.distance", DistanceRecord::class),
        TypeSpec("activity.exercise_session", ExerciseSessionRecord::class),
        TypeSpec("activity.exercise_power", PowerRecord::class),
        TypeSpec("activity.speed", SpeedRecord::class),
        TypeSpec("activity.vo2_max", Vo2MaxRecord::class),
        TypeSpec("vitals.heart_rate", HeartRateRecord::class),
        TypeSpec("vitals.oxygen_saturation", OxygenSaturationRecord::class),
        TypeSpec("vitals.blood_pressure", BloodPressureRecord::class),
        TypeSpec("vitals.blood_glucose", BloodGlucoseRecord::class),
        TypeSpec("sleep.session", SleepSessionRecord::class),
        TypeSpec("body.weight", WeightRecord::class),
        TypeSpec("body.body_fat", BodyFatRecord::class),
        TypeSpec("body.basal_metabolic_rate", BasalMetabolicRateRecord::class),
        TypeSpec("body.height", HeightRecord::class),
        TypeSpec("nutrition.intake", NutritionRecord::class),
    )

    suspend fun sync(): String {
        val pairing = store.pairing() ?: error("Pair this phone before syncing.")
        store.migrateHeartRateToAllOrigins()
        val granted = client.permissionController.getGrantedPermissions()
        val started = Instant.now()
        var uploaded = 0
        val failed = mutableListOf<String>()
        for (spec in specs) {
            val permission = HealthPermission.getReadPermission(spec.type)
            if (permission !in granted) continue
            try {
                uploaded += syncType(spec, pairing)
            } catch (error: Exception) {
                val reason = error.message?.replace(Regex("\\s+"), " ")?.take(140) ?: "no message"
                failed += "${spec.metric} (${error::class.simpleName}: $reason)"
            }
        }
        pairing.identityNamespaceId?.let { namespace ->
            try {
                val samsung = SamsungHealthDataAdapterFactory.create(context, store).collect()
                uploadSamsung(pairing, namespace, samsung)
                samsung.checkpointUpdates.forEach { (key, value) ->
                    store.setCheckpoint(key, value)
                }
                uploaded += samsung.sourceChanges.size
            } catch (error: Exception) {
                val reason = error.message?.replace(Regex("\\s+"), " ")?.take(140) ?: "no message"
                failed += "Samsung Health Data adapter status (${error::class.simpleName}: $reason)"
            }
        }
        val result = "${Instant.now()} | $uploaded changes uploaded${if (failed.isNotEmpty()) "; retry: ${failed.joinToString()}" else ""}"
        store.setStatus(result)
        return result
    }

    private suspend fun uploadSamsung(
        pairing: PairingConfig,
        namespace: String,
        result: SamsungAdapterResult,
    ) {
        val chunks = result.sourceChanges.chunked(SAMSUNG_CHANGES_PER_BATCH)
        if (chunks.isEmpty()) {
            val batch = SamsungAvailabilityBatch(
                installationId = store.installationId(),
                identityNamespaceId = namespace,
                adapter = result.adapter,
                sourceChanges = emptyList(),
                availability = result.availability,
            )
            check(uploader.upload(pairing, batch)) { "Samsung Health Data status upload failed." }
            return
        }
        chunks.forEachIndexed { index, changes ->
            uploadSamsungChunk(
                pairing,
                namespace,
                result.adapter,
                changes,
                if (index == 0) result.availability else emptyList(),
            )
        }
    }

    private suspend fun uploadSamsungChunk(
        pairing: PairingConfig,
        namespace: String,
        adapter: JSONObject,
        changes: List<JSONObject>,
        availability: List<SamsungAdapterAvailability>,
    ) {
        val batch = SamsungAvailabilityBatch(
            installationId = store.installationId(),
            identityNamespaceId = namespace,
            adapter = adapter,
            sourceChanges = changes,
            availability = availability,
        )
        if (batch.bytes().size > MAX_BATCH_BYTES && changes.size > 1) {
            val middle = changes.size / 2
            uploadSamsungChunk(pairing, namespace, adapter, changes.subList(0, middle), availability)
            uploadSamsungChunk(pairing, namespace, adapter, changes.subList(middle, changes.size), emptyList())
            return
        }
        check(batch.bytes().size <= MAX_BATCH_BYTES) { "A Samsung Health source record exceeds the server contract." }
        check(uploader.upload(pairing, batch)) { "Samsung Health Data upload failed." }
    }

    private suspend fun syncType(spec: TypeSpec<out Record>, pairing: PairingConfig): Int {
        val checkpoint = store.checkpoint(spec.metric)
        var uploaded = if (checkpoint == null) initialBackfill(spec, pairing) else changesSince(spec, checkpoint, pairing)
        if (checkpoint != null && spec.metric == "vitals.heart_rate") {
            uploaded += reconcileRecent(spec, pairing, days = 3)
        }
        return if (spec.metric == "activity.steps") {
            uploaded + syncStepAggregates(pairing, if (checkpoint == null) 30 else 2)
        } else {
            uploaded
        }
    }

    private fun originFilters(spec: TypeSpec<out Record>): Set<DataOrigin> =
        if (spec.metric == "vitals.heart_rate") emptySet() else setOf(DataOrigin(SAMSUNG_HEALTH_PACKAGE))

    private suspend fun initialBackfill(spec: TypeSpec<out Record>, pairing: PairingConfig): Int {
        val now = Instant.now()
        val start = now.minus(30, ChronoUnit.DAYS)
        val token = client.getChangesToken(
            ChangesTokenRequest(
                recordTypes = setOf(spec.type),
                dataOriginFilters = originFilters(spec),
            ),
        )
        val changes = mutableListOf<JSONObject>()
        var pageToken: String? = null
        var hasRecords = false
        var uploaded = 0
        do {
            val response = client.readRecords(
                ReadRecordsRequest(
                    recordType = spec.type,
                    timeRangeFilter = TimeRangeFilter.between(start, now),
                    dataOriginFilter = originFilters(spec),
                    pageSize = 500,
                    pageToken = pageToken,
                ),
            )
            if (spec.metric != "activity.steps") {
                response.records.forEach { record ->
                    if (record is HeartRateRecord && record.samples.isEmpty()) return@forEach
                    changes += upsert(normalize(record, spec.metric, now))
                    hasRecords = true
                }
            }
            pageToken = response.pageToken
            if (changes.size >= 450) {
                uploadChanges(pairing, changes.toList(), availability(spec.metric, changes.isNotEmpty(), start, now, backfillLimited = true))
                uploaded += changes.size
                changes.clear()
            }
        } while (!pageToken.isNullOrEmpty())

        uploadChanges(pairing, changes, availability(spec.metric, hasRecords, start, now, backfillLimited = true))
        uploaded += changes.size
        store.setCheckpoint(spec.metric, token)
        return uploaded
    }

    private suspend fun reconcileRecent(spec: TypeSpec<out Record>, pairing: PairingConfig, days: Long): Int {
        val now = Instant.now()
        val start = now.minus(days, ChronoUnit.DAYS)
        val changes = mutableListOf<JSONObject>()
        var pageToken: String? = null
        var hasRecords = false
        var uploaded = 0
        do {
            val response = client.readRecords(
                ReadRecordsRequest(
                    recordType = spec.type,
                    timeRangeFilter = TimeRangeFilter.between(start, now),
                    dataOriginFilter = originFilters(spec),
                    pageSize = 500,
                    pageToken = pageToken,
                ),
            )
            response.records.forEach { record ->
                if (record is HeartRateRecord && record.samples.isEmpty()) return@forEach
                changes += upsert(normalize(record, spec.metric, now))
                hasRecords = true
            }
            pageToken = response.pageToken
            if (changes.size >= 450) {
                uploadChanges(pairing, changes.toList(), availability(spec.metric, true, start, now, reconciling = true))
                uploaded += changes.size
                changes.clear()
            }
        } while (!pageToken.isNullOrEmpty())
        uploadChanges(pairing, changes, availability(spec.metric, hasRecords, start, now, reconciling = true))
        uploaded += changes.size
        return uploaded
    }

    private suspend fun changesSince(spec: TypeSpec<out Record>, token: String, pairing: PairingConfig): Int {
        var currentToken = token
        var count = 0
        try {
            do {
                val response = client.getChanges(currentToken)
                val changes = response.changes.mapNotNull { change ->
                    when (change) {
                        is UpsertionChange -> {
                            val record = change.record
                            if (spec.metric != "activity.steps" &&
                                (spec.metric == "vitals.heart_rate" || record.metadata.dataOrigin.packageName == SAMSUNG_HEALTH_PACKAGE) &&
                                !(record is HeartRateRecord && record.samples.isEmpty())
                            ) {
                                upsert(normalize(record, spec.metric, Instant.now()))
                            } else null
                        }
                        is DeletionChange -> if (spec.metric == "activity.steps") null else deletion(change.recordId, spec.metric, null)
                        else -> null
                    }
                }
                if (changes.isNotEmpty()) {
                    uploadChanges(
                        pairing,
                        changes,
                        availability(spec.metric, true, null, Instant.now()),
                    )
                    count += changes.size
                }
                currentToken = response.nextChangesToken
                store.setCheckpoint(spec.metric, currentToken)
            } while (response.hasMore)
        } catch (error: IllegalArgumentException) {
            store.clearCheckpoint(spec.metric)
            uploadChanges(
                pairing,
                emptyList(),
                availability(spec.metric, false, null, Instant.now(), reconciling = true, interrupted = true),
            )
            throw error
        }
        return count
    }

    private suspend fun syncStepAggregates(pairing: PairingConfig, dayCount: Int): Int {
        val now = Instant.now()
        val zone = ZoneId.systemDefault()
        val today = now.atZone(zone).toLocalDate()
        val changes = mutableListOf<JSONObject>()
        for (daysAgo in (dayCount - 1) downTo 0) {
            val date = today.minusDays(daysAgo.toLong())
            val start = date.atStartOfDay(zone).toInstant()
            val nextStart = date.plusDays(1).atStartOfDay(zone).toInstant()
            val end = minOf(nextStart, now)
            if (end <= start) continue
            val result = client.aggregate(
                AggregateRequest(
                    metrics = setOf(StepsRecord.COUNT_TOTAL),
                    timeRangeFilter = TimeRangeFilter.between(start, end),
                    dataOriginFilter = setOf(DataOrigin(SAMSUNG_HEALTH_PACKAGE)),
                ),
            )
            val count = result[StepsRecord.COUNT_TOTAL] ?: continue
            val observation = JSONObject()
                .put("record_id", "steps-daily-$date")
                .put("metric", "activity.steps")
                .put("record_kind", "aggregate")
                .put("start_at", start.toString())
                .put("end_at", end.toString())
                .put("start_zone_offset", zone.rules.getOffset(start).id)
                .put("end_zone_offset", zone.rules.getOffset(end).id)
                .put("observed_by_companion_at", now.toString())
                .put("upstream_last_modified_at", now.toString())
                .put("source_package", SAMSUNG_HEALTH_PACKAGE)
                .put("recording_method", "unknown")
                .put("attribution", JSONObject()
                    .put("state", "samsung_health_unattributed")
                    .put("evidence", "Samsung Health daily aggregate; Health Connect aggregation does not retain source-device metadata."))
                .put("payload", JSONObject()
                    .put("value", count)
                    .put("unit", "count")
                    .put("aggregation", "health_connect"))
            changes += upsert(observation)
        }
        uploadChanges(
            pairing,
            changes,
            availability("activity.steps", changes.isNotEmpty(), today.minusDays((dayCount - 1).toLong()).atStartOfDay(zone).toInstant(), now, backfillLimited = dayCount >= 30),
        )
        return changes.size
    }

    private suspend fun uploadChanges(pairing: PairingConfig, changes: List<JSONObject>, availability: AvailabilityReport) {
        if (changes.isEmpty()) {
            uploadChunk(pairing, emptyList(), availability)
            return
        }
        changes.chunked(100).forEach { chunk -> uploadChunk(pairing, chunk, availability) }
    }

    private suspend fun uploadChunk(pairing: PairingConfig, changes: List<JSONObject>, availability: AvailabilityReport) {
        val batch = WatchBatch(store.installationId(), changes, listOf(availability))
        if (batch.bytes().size > MAX_BATCH_BYTES && changes.size > 1) {
            val middle = changes.size / 2
            uploadChunk(pairing, changes.subList(0, middle), availability)
            uploadChunk(pairing, changes.subList(middle, changes.size), availability)
            return
        }
        check(batch.bytes().size <= MAX_BATCH_BYTES) { "A single Health Connect record exceeds the server contract." }
        check(uploader.upload(pairing, batch)) { "Upload failed; Health Connect will be reread." }
    }

    private fun availability(
        metric: String,
        hasRecords: Boolean,
        start: Instant?,
        end: Instant?,
        backfillLimited: Boolean = false,
        interrupted: Boolean = false,
        reconciling: Boolean = false,
    ) = AvailabilityReport(
        metric = metric,
        state = if (hasRecords) "available" else "no_observation",
        evidence = if (hasRecords) {
            if (metric == "vitals.heart_rate") {
                "Read permission granted and Health Connect heart-rate records were returned with their source attribution."
            } else {
                "Read permission granted and Samsung Health-origin records were returned."
            }
        } else {
            if (metric == "vitals.heart_rate") {
                "Read permission granted; no heart-rate record from any Health Connect origin was returned in the checked window."
            } else {
                "Read permission granted; no Samsung Health-origin record was returned in the checked window."
            }
        },
        checkedAt = Instant.now(),
        windowStart = start,
        windowEnd = end,
        backfillLimited = backfillLimited,
        interrupted = interrupted,
        reconciling = reconciling,
    )

    private fun normalize(record: Record, metric: String, observedAt: Instant): JSONObject {
        val payload = when (record) {
            is StepsRecord -> JSONObject().put("value", record.count).put("unit", "count").put("aggregation", "raw_interval")
            is TotalCaloriesBurnedRecord -> value(record.energy.inKilocalories, "kcal")
            is DistanceRecord -> value(record.distance.inMeters, "m")
            is ExerciseSessionRecord -> JSONObject().put("exercise_type", record.exerciseType.toString()).put("title", record.title ?: JSONObject.NULL)
            is PowerRecord -> series(record.samples.map { Triple(it.time, it.power.inWatts, "W") })
            is SpeedRecord -> series(record.samples.map { Triple(it.time, it.speed.inMetersPerSecond, "m/s") })
            is Vo2MaxRecord -> JSONObject().put("value", record.vo2MillilitersPerMinuteKilogram).put("unit", "mL/kg/min").put("measurement_method", record.measurementMethod.toString())
            is HeartRateRecord -> series(heartSamples(record).map { Triple(it.time, it.beatsPerMinute.toDouble(), "bpm") })
            is OxygenSaturationRecord -> value(record.percentage.value, "%")
            is BloodPressureRecord -> JSONObject()
                .put("systolic", record.systolic.inMillimetersOfMercury)
                .put("diastolic", record.diastolic.inMillimetersOfMercury)
                .put("unit", "mmHg")
                .put("body_position", record.bodyPosition.toString())
                .put("measurement_site", record.measurementLocation.toString())
            is BloodGlucoseRecord -> JSONObject()
                .put("value", record.level.inMilligramsPerDeciliter)
                .put("unit", "mg/dL")
                .put("specimen_source", record.specimenSource.toString())
                .put("relation_to_meal", record.relationToMeal.toString())
            is SleepSessionRecord -> JSONObject()
                .put("title", record.title ?: JSONObject.NULL)
                .put("stages", JSONArray(record.stages.map { stage ->
                    JSONObject()
                        .put("start_at", stage.startTime.toString())
                        .put("end_at", stage.endTime.toString())
                        .put("stage", sleepStage(stage.stage))
                }))
            is WeightRecord -> value(record.weight.inKilograms, "kg")
            is BodyFatRecord -> value(record.percentage.value, "%")
            is BasalMetabolicRateRecord -> value(record.basalMetabolicRate.inKilocaloriesPerDay, "kcal/day")
            is HeightRecord -> value(record.height.inMeters * 100.0, "cm")
            is NutritionRecord -> JSONObject()
                .put("meal_type", record.mealType.toString())
                .put("name", record.name ?: JSONObject.NULL)
                .put("energy_kcal", record.energy?.inKilocalories ?: JSONObject.NULL)
                .put("nutrients", JSONObject()
                    .put("protein_g", record.protein?.inGrams ?: JSONObject.NULL)
                    .put("carbohydrate_g", record.totalCarbohydrate?.inGrams ?: JSONObject.NULL)
                    .put("fat_g", record.totalFat?.inGrams ?: JSONObject.NULL)
                    .put("fiber_g", record.dietaryFiber?.inGrams ?: JSONObject.NULL)
                    .put("sugar_g", record.sugar?.inGrams ?: JSONObject.NULL)
                    .put("sodium_mg", record.sodium?.inGrams?.times(1000) ?: JSONObject.NULL))
            else -> error("Unsupported record type for $metric")
        }
        val kind = when (record) {
            is HeartRateRecord, is PowerRecord, is SpeedRecord -> "series"
            is SleepSessionRecord, is ExerciseSessionRecord -> "session"
            is BloodPressureRecord -> "composite"
            is StepsRecord, is TotalCaloriesBurnedRecord, is DistanceRecord, is NutritionRecord -> "interval"
            else -> "point"
        }
        val json = JSONObject()
            .put("record_id", record.metadata.id)
            .put("metric", metric)
            .put("record_kind", kind)
            .put("observed_by_companion_at", observedAt.toString())
            .put("upstream_last_modified_at", record.metadata.lastModifiedTime.toString())
            .put("source_package", record.metadata.dataOrigin.packageName)
            .put("recording_method", recordingMethod(record.metadata.recordingMethod))
            .put("attribution", attribution(record.metadata))
            .put("payload", payload)
        when (record) {
            is StepsRecord -> interval(json, record.startTime, record.endTime, record.startZoneOffset?.id, record.endZoneOffset?.id)
            is TotalCaloriesBurnedRecord -> interval(json, record.startTime, record.endTime, record.startZoneOffset?.id, record.endZoneOffset?.id)
            is DistanceRecord -> interval(json, record.startTime, record.endTime, record.startZoneOffset?.id, record.endZoneOffset?.id)
            is ExerciseSessionRecord -> interval(json, record.startTime, record.endTime, record.startZoneOffset?.id, record.endZoneOffset?.id)
            is PowerRecord -> interval(json, record.startTime, record.endTime, record.startZoneOffset?.id, record.endZoneOffset?.id)
            is SpeedRecord -> interval(json, record.startTime, record.endTime, record.startZoneOffset?.id, record.endZoneOffset?.id)
            is HeartRateRecord -> {
                val samples = heartSamples(record)
                interval(json, samples.first().time, samples.last().time, record.startZoneOffset?.id, record.endZoneOffset?.id)
            }
            is SleepSessionRecord -> interval(json, record.startTime, record.endTime, record.startZoneOffset?.id, record.endZoneOffset?.id)
            is NutritionRecord -> interval(json, record.startTime, record.endTime, record.startZoneOffset?.id, record.endZoneOffset?.id)
            is Vo2MaxRecord -> point(json, record.time, record.zoneOffset?.id)
            is OxygenSaturationRecord -> point(json, record.time, record.zoneOffset?.id)
            is BloodPressureRecord -> point(json, record.time, record.zoneOffset?.id)
            is BloodGlucoseRecord -> point(json, record.time, record.zoneOffset?.id)
            is WeightRecord -> point(json, record.time, record.zoneOffset?.id)
            is BodyFatRecord -> point(json, record.time, record.zoneOffset?.id)
            is BasalMetabolicRateRecord -> point(json, record.time, record.zoneOffset?.id)
            is HeightRecord -> point(json, record.time, record.zoneOffset?.id)
        }
        return json
    }

    private fun value(number: Double, unit: String) = JSONObject().put("value", number).put("unit", unit)

    private fun heartSamples(record: HeartRateRecord) = record.samples
        .sortedBy { it.time }
        .takeLast(MAX_SERIES_SAMPLES)

    private fun series(samples: List<Triple<Instant, Double, String>>) = JSONObject().put(
        "samples",
        JSONArray(samples.map { (time, number, unit) -> JSONObject().put("time", time.toString()).put("value", number).put("unit", unit) }),
    )

    private fun point(json: JSONObject, time: Instant, offset: String?) {
        json.put("measured_at", time.toString()).put("zone_offset", offset ?: JSONObject.NULL)
    }

    private fun interval(json: JSONObject, start: Instant, end: Instant, startOffset: String?, endOffset: String?) {
        json.put("start_at", start.toString())
            .put("end_at", end.toString())
            .put("start_zone_offset", startOffset ?: JSONObject.NULL)
            .put("end_zone_offset", endOffset ?: JSONObject.NULL)
    }

    private fun attribution(metadata: Metadata): JSONObject {
        val model = metadata.device?.model.orEmpty()
        val type = metadata.device?.type?.toString()
        val method = recordingMethod(metadata.recordingMethod)
        val confirmed = model.contains("Watch5", ignoreCase = true) || model.startsWith("SM-R9", ignoreCase = true)
        val state = when {
            confirmed -> "watch_confirmed"
            method == "manual" -> "manual"
            else -> "samsung_health_unattributed"
        }
        val evidence = when (state) {
            "watch_confirmed" -> "Health Connect metadata identifies Galaxy Watch5-series model $model."
            "manual" -> "Health Connect recording method is manual."
            else -> "Samsung Health origin; available metadata does not prove the source device."
        }
        return JSONObject()
            .put("state", state)
            .put("evidence", evidence)
            .put("device_type", type ?: JSONObject.NULL)
            .put("device_model", if (model.isBlank()) JSONObject.NULL else model)
    }

    private fun recordingMethod(value: Int): String = when (value) {
        Metadata.RECORDING_METHOD_ACTIVELY_RECORDED -> "active"
        Metadata.RECORDING_METHOD_AUTOMATICALLY_RECORDED -> "automatic"
        Metadata.RECORDING_METHOD_MANUAL_ENTRY -> "manual"
        else -> "unknown"
    }

    private fun sleepStage(value: Int): String = when (value) {
        SleepSessionRecord.STAGE_TYPE_AWAKE -> "awake"
        SleepSessionRecord.STAGE_TYPE_SLEEPING -> "sleeping"
        SleepSessionRecord.STAGE_TYPE_OUT_OF_BED -> "out_of_bed"
        SleepSessionRecord.STAGE_TYPE_LIGHT -> "light"
        SleepSessionRecord.STAGE_TYPE_DEEP -> "deep"
        SleepSessionRecord.STAGE_TYPE_REM -> "rem"
        else -> "unknown"
    }

    companion object {
        const val SAMSUNG_HEALTH_PACKAGE = "com.sec.android.app.shealth"
        private const val MAX_BATCH_BYTES = 1024 * 1024
        private const val MAX_SERIES_SAMPLES = 2000
        private const val SAMSUNG_CHANGES_PER_BATCH = 20

        val READ_PERMISSIONS: Set<String> = setOf(
            HealthPermission.getReadPermission(StepsRecord::class),
            HealthPermission.getReadPermission(TotalCaloriesBurnedRecord::class),
            HealthPermission.getReadPermission(DistanceRecord::class),
            HealthPermission.getReadPermission(ExerciseSessionRecord::class),
            HealthPermission.getReadPermission(PowerRecord::class),
            HealthPermission.getReadPermission(SpeedRecord::class),
            HealthPermission.getReadPermission(Vo2MaxRecord::class),
            HealthPermission.getReadPermission(HeartRateRecord::class),
            HealthPermission.getReadPermission(OxygenSaturationRecord::class),
            HealthPermission.getReadPermission(BloodPressureRecord::class),
            HealthPermission.getReadPermission(BloodGlucoseRecord::class),
            HealthPermission.getReadPermission(SleepSessionRecord::class),
            HealthPermission.getReadPermission(WeightRecord::class),
            HealthPermission.getReadPermission(BodyFatRecord::class),
            HealthPermission.getReadPermission(BasalMetabolicRateRecord::class),
            HealthPermission.getReadPermission(HeightRecord::class),
            HealthPermission.getReadPermission(NutritionRecord::class),
        )
    }
}
