package ai.clinicianassist.personalstate

import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.util.UUID

/**
 * Boundary for the optional licensed Samsung Health Data SDK integration.
 * The public build contains only the unavailable implementation and no Samsung SDK binary.
 */
interface SamsungHealthDataAdapter {
    suspend fun collect(): SamsungAdapterResult
}

data class SamsungAdapterAvailability(
    val metric: String,
    val state: String,
    val evidence: String,
)

private fun SamsungAdapterAvailability.toJson(adapter: JSONObject, checkedAt: Instant): JSONObject = JSONObject().apply {
    put("adapter", adapter)
    put("metric", metric)
    put("state", state)
    put("evidence", evidence)
    put("coverage_checked_at", checkedAt.toString())
    put("coverage", JSONObject().apply {
        put("window_start", JSONObject.NULL)
        put("window_end", JSONObject.NULL)
        put("truncated", false)
        put("backfill_limited", false)
        put("interrupted", false)
        put("reconciling", false)
    })
}

data class SamsungAdapterResult(
    val sourceChanges: List<String>,
    val availability: List<SamsungAdapterAvailability>,
)

data class SamsungAvailabilityBatch(
    val installationId: String,
    val identityNamespaceId: String,
    val availability: List<SamsungAdapterAvailability>,
    val generatedAt: Instant = Instant.now(),
    override val batchId: String = UUID.randomUUID().toString(),
) : UploadBatch {
    private val adapter = JSONObject().apply {
        put("id", "android_samsung_health_data")
        put("version", "not-installed")
    }

    fun toJson(): JSONObject = JSONObject().apply {
        put("schema_version", "personal-state-watch-batch/v2")
        put("adapter", adapter)
        put("identity_namespace_id", identityNamespaceId)
        put("batch_id", batchId)
        put("installation_id", installationId)
        put("generated_at", generatedAt.toString())
        put("changes", JSONArray())
        put("availability", JSONArray(availability.map { it.toJson(adapter, generatedAt) }))
    }

    override fun bytes(): ByteArray = toJson().toString().toByteArray(Charsets.UTF_8)
}

class SamsungHealthDataUnavailableAdapter : SamsungHealthDataAdapter {
    override suspend fun collect(): SamsungAdapterResult = SamsungAdapterResult(
        sourceChanges = emptyList(),
        availability = METRICS.map { metric ->
            SamsungAdapterAvailability(
                metric = metric,
                state = "adapter_not_installed",
                evidence = "This public build does not contain the licensed Samsung Health Data SDK adapter.",
            )
        },
    )

    private companion object {
        val METRICS = listOf(
            "vitals.oxygen_saturation_series",
            "sleep.samsung_session",
            "sleep.summary",
            "sleep.score",
            "vitals.skin_temperature",
            "wellness.energy_score",
            "cardiac.irregular_rhythm_notification",
            "sleep.apnea_detected_sign",
            "activity.floors",
            "activity.active_time",
            "sleep.snoring",
            "cardiac.ecg",
            "wellness.stress",
        )
    }
}
