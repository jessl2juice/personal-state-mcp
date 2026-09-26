package ai.clinicianassist.personalstate

import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.util.UUID

interface UploadBatch {
    val batchId: String
    fun bytes(): ByteArray
}

data class AvailabilityReport(
    val metric: String,
    val state: String,
    val evidence: String,
    val checkedAt: Instant,
    val windowStart: Instant?,
    val windowEnd: Instant?,
    val truncated: Boolean = false,
    val backfillLimited: Boolean = false,
    val interrupted: Boolean = false,
    val reconciling: Boolean = false,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("metric", metric)
        put("state", state)
        put("evidence", evidence)
        put("checked_at", checkedAt.toString())
        put("coverage", JSONObject().apply {
            put("window_start", windowStart?.toString() ?: JSONObject.NULL)
            put("window_end", windowEnd?.toString() ?: JSONObject.NULL)
            put("truncated", truncated)
            put("backfill_limited", backfillLimited)
            put("interrupted", interrupted)
            put("reconciling", reconciling)
        })
    }
}

data class WatchBatch(
    val installationId: String,
    val changes: List<JSONObject>,
    val availability: List<AvailabilityReport>,
    val generatedAt: Instant = Instant.now(),
    override val batchId: String = UUID.randomUUID().toString(),
) : UploadBatch {
    fun toJson(): JSONObject = JSONObject().apply {
        put("schema_version", "personal-state-watch-batch/v1")
        put("batch_id", batchId)
        put("installation_id", installationId)
        put("generated_at", generatedAt.toString())
        put("changes", JSONArray(changes))
        put("availability", JSONArray(availability.map { it.toJson() }))
    }

    override fun bytes(): ByteArray = toJson().toString().toByteArray(Charsets.UTF_8)
}

fun upsert(observation: JSONObject): JSONObject = JSONObject().apply {
    put("operation", "upsert")
    put("observation", observation)
}

fun deletion(recordId: String, metric: String, modifiedAt: Instant?): JSONObject = JSONObject().apply {
    put("operation", "delete")
    put("record_id", recordId)
    put("metric", metric)
    put("source_package", HealthConnectSync.SAMSUNG_HEALTH_PACKAGE)
    put("upstream_last_modified_at", modifiedAt?.toString() ?: JSONObject.NULL)
}
