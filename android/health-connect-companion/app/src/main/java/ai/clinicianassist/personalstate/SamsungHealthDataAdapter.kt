package ai.clinicianassist.personalstate

import android.app.Activity
import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.util.UUID

/**
 * Boundary for the optional licensed Samsung Health Data SDK integration.
 * The public build contains only the unavailable implementation and no Samsung SDK binary.
 */
interface SamsungHealthDataAdapter {
    val installed: Boolean
    suspend fun requestPermissions(activity: Activity): SamsungPermissionResult
    suspend fun collect(): SamsungAdapterResult
}

data class SamsungPermissionResult(
    val granted: Int,
    val requested: Int,
    val message: String,
)

data class SamsungAdapterAvailability(
    val metric: String,
    val state: String,
    val evidence: String,
    val windowStart: Instant? = null,
    val windowEnd: Instant? = null,
    val truncated: Boolean = false,
    val backfillLimited: Boolean = false,
    val interrupted: Boolean = false,
    val reconciling: Boolean = false,
)

private fun SamsungAdapterAvailability.toJson(adapter: JSONObject, checkedAt: Instant): JSONObject = JSONObject().apply {
    put("adapter", adapter)
    put("metric", metric)
    put("state", state)
    put("evidence", evidence)
    put("coverage_checked_at", checkedAt.toString())
    put("coverage", JSONObject().apply {
        put("window_start", windowStart?.toString() ?: JSONObject.NULL)
        put("window_end", windowEnd?.toString() ?: JSONObject.NULL)
        put("truncated", truncated)
        put("backfill_limited", backfillLimited)
        put("interrupted", interrupted)
        put("reconciling", reconciling)
    })
}

data class SamsungAdapterResult(
    val adapter: JSONObject,
    val sourceChanges: List<JSONObject>,
    val availability: List<SamsungAdapterAvailability>,
    val checkpointUpdates: Map<String, String> = emptyMap(),
)

data class SamsungAvailabilityBatch(
    val installationId: String,
    val identityNamespaceId: String,
    val adapter: JSONObject,
    val sourceChanges: List<JSONObject>,
    val availability: List<SamsungAdapterAvailability>,
    val generatedAt: Instant = Instant.now(),
    override val batchId: String = UUID.randomUUID().toString(),
) : UploadBatch {
    fun toJson(): JSONObject = JSONObject().apply {
        put("schema_version", "personal-state-watch-batch/v2")
        put("adapter", adapter)
        put("identity_namespace_id", identityNamespaceId)
        put("batch_id", batchId)
        put("installation_id", installationId)
        put("generated_at", generatedAt.toString())
        put("changes", JSONArray(sourceChanges))
        put("availability", JSONArray(availability.map { it.toJson(adapter, generatedAt) }))
    }

    override fun bytes(): ByteArray = toJson().toString().toByteArray(Charsets.UTF_8)
}

object SamsungHealthDataAdapterFactory {
    private const val IMPLEMENTATION =
        "ai.clinicianassist.personalstate.SamsungHealthDataSdkAdapter"

    fun create(context: Context, store: PairingStore): SamsungHealthDataAdapter {
        val implementation = try {
            Class.forName(IMPLEMENTATION)
        } catch (_: ClassNotFoundException) {
            return SamsungHealthDataUnavailableAdapter()
        }
        return runCatching {
        val constructor = implementation.getConstructor(Context::class.java, PairingStore::class.java)
        constructor.newInstance(context.applicationContext, store) as SamsungHealthDataAdapter
        }.getOrElse { error ->
            SamsungHealthDataInitializationFailedAdapter(error.cause?.javaClass?.simpleName ?: error.javaClass.simpleName)
        }
    }
}

class SamsungHealthDataUnavailableAdapter : SamsungHealthDataAdapter {
    override val installed = false

    override suspend fun requestPermissions(activity: Activity) = SamsungPermissionResult(
        granted = 0,
        requested = SAMSUNG_METRICS.size,
        message = "This installation does not contain the licensed Samsung Health reader.",
    )

    override suspend fun collect(): SamsungAdapterResult = SamsungAdapterResult(
        adapter = JSONObject().put("id", "android_samsung_health_data").put("version", "not-installed"),
        sourceChanges = emptyList(),
        availability = SAMSUNG_METRICS.map { metric ->
            SamsungAdapterAvailability(
                metric = metric,
                state = "adapter_not_installed",
                evidence = "This public build does not contain the licensed Samsung Health Data SDK adapter.",
            )
        },
    )

}

private class SamsungHealthDataInitializationFailedAdapter(
    private val failureClass: String,
) : SamsungHealthDataAdapter {
    override val installed = true

    override suspend fun requestPermissions(activity: Activity) = SamsungPermissionResult(
        granted = 0,
        requested = SAMSUNG_METRICS.size,
        message = "Samsung Health could not initialize on this phone ($failureClass).",
    )

    override suspend fun collect() = SamsungAdapterResult(
        adapter = JSONObject().put("id", "android_samsung_health_data").put("version", "1.1.0"),
        sourceChanges = emptyList(),
        availability = SAMSUNG_METRICS.map { metric ->
            SamsungAdapterAvailability(
                metric,
                "platform_feature_unavailable",
                "The licensed reader is installed, but Samsung Health initialization failed ($failureClass).",
            )
        },
    )
}

private val SAMSUNG_METRICS = listOf(
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
