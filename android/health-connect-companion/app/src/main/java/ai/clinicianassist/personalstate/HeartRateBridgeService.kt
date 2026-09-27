package ai.clinicianassist.personalstate

import android.content.Context
import android.os.Build
import com.google.android.gms.wearable.DataEvent
import com.google.android.gms.wearable.DataEventBuffer
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.WearableListenerService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.util.TreeMap
import kotlin.math.abs

class HeartRateBridgeService : WearableListenerService() {
    override fun onMessageReceived(event: MessageEvent) {
        if (event.path != MESSAGE_PATH) return
        handlePayload(event.data)
    }

    override fun onDataChanged(events: DataEventBuffer) {
        events
            .asSequence()
            .filter { it.type == DataEvent.TYPE_CHANGED && it.dataItem.uri.path == MESSAGE_PATH }
            .mapNotNull { it.dataItem.data }
            .forEach(::handlePayload)
    }

    private fun handlePayload(payload: ByteArray) {
        val store = PairingStore(this)
        if (store.pairing() == null) {
            store.setLiveHeartStatus("Watch connected, but this phone is not paired for upload.")
            return
        }
        val reading = runCatching { HeartRatePayload.parse(payload) }.getOrElse {
            store.setLiveHeartStatus("Watch sent an invalid heart-rate reading.")
            return
        }
        if (reading.measuredAt.toEpochMilli() <= store.lastUploadedHeartEpochMs()) return
        LiveHeartUploadCoordinator.enqueue(this, reading)
    }

    companion object {
        const val MESSAGE_PATH = "/personal-state/heart-rate/v1"
        const val DIRECT_WEAR_PACKAGE = "ai.clinicianassist.personalstate"
    }
}

data class DirectHeartReading(val bpm: Double, val measuredAt: Instant, val model: String)

object LiveHeartUploadCoordinator {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val lock = Any()
    private val pending = TreeMap<Long, DirectHeartReading>()
    private var uploadJob: Job? = null

    fun enqueue(context: Context, reading: DirectHeartReading) {
        val appContext = context.applicationContext
        synchronized(lock) {
            pending[reading.measuredAt.toEpochMilli()] = reading
            if (uploadJob?.isActive != true) {
                uploadJob = scope.launch { drain(appContext) }
            }
        }
    }

    private suspend fun drain(context: Context) {
        val store = PairingStore(context)
        val pairing = store.pairing() ?: run {
            store.setLiveHeartStatus("Watch connected, but this phone is not paired for upload.")
            return
        }
        while (true) {
            val readings = synchronized(lock) {
                if (pending.isEmpty()) return
                pending.values.toList().also { pending.clear() }
            }
            val latest = readings.last()
            val pendingPayload = HeartRatePayload.encode(latest)
            store.setPendingHeartReading(pendingPayload)
            runCatching { LiveHeartUpload.upload(store, pairing, readings) }
                .onSuccess {
                    store.markHeartReadingUploaded(latest.measuredAt.toEpochMilli(), latest.bpm)
                    store.clearPendingHeartReading(pendingPayload)
                    store.setLiveHeartStatus("Live watch heart rate uploaded at ${latest.measuredAt}.")
                }
                .onFailure { error ->
                    val newest = synchronized(lock) {
                        pending.values.lastOrNull()?.also { pending.clear() }
                    }
                    if (newest != null) store.setPendingHeartReading(HeartRatePayload.encode(newest))
                    val reason = error.message?.replace(Regex("\\s+"), " ")?.take(180)
                        ?: error::class.simpleName
                    store.setLiveHeartStatus("Live watch upload queued after failure: $reason")
                    HeartRateRetryWorker.enqueue(context)
                    return
                }
        }
    }
}

object HeartRatePayload {
    fun parse(
        bytes: ByteArray,
        now: Instant = Instant.now(),
        maxAgeSeconds: Long? = 300,
    ): DirectHeartReading {
        require(bytes.size in 2..4096)
        val json = JSONObject(String(bytes, Charsets.UTF_8))
        val bpm = json.getDouble("bpm")
        require(bpm in 20.0..240.0)
        val measuredAt = Instant.parse(json.getString("measured_at"))
        if (maxAgeSeconds != null) require(abs(now.epochSecond - measuredAt.epochSecond) <= maxAgeSeconds)
        val model = json.optString("device_model", Build.MODEL).take(80)
        return DirectHeartReading(bpm, measuredAt, model)
    }

    fun encode(reading: DirectHeartReading): String = JSONObject()
        .put("bpm", reading.bpm)
        .put("measured_at", reading.measuredAt.toString())
        .put("device_model", reading.model)
        .toString()
}

object LiveHeartUpload {
    suspend fun upload(store: PairingStore, pairing: PairingConfig, reading: DirectHeartReading) {
        upload(store, pairing, listOf(reading))
    }

    suspend fun upload(store: PairingStore, pairing: PairingConfig, readings: List<DirectHeartReading>) {
        require(readings.isNotEmpty())
        val ordered = readings
            .distinctBy { it.measuredAt.toEpochMilli() }
            .sortedBy { it.measuredAt }
        val observedAt = Instant.now()
        val observations = ordered.map { reading -> heartObservation(reading, observedAt) }
        val availability = AvailabilityReport(
            metric = "vitals.heart_rate",
            state = "available",
            evidence = "Direct Wear OS heart-rate samples were received from the paired watch.",
            checkedAt = observedAt,
            windowStart = ordered.first().measuredAt,
            windowEnd = ordered.last().measuredAt,
        )
        val batch = WatchBatch(store.installationId(), observations.map(::upsert), listOf(availability))
        check(UploadClient().upload(pairing, batch))
    }

    private fun heartObservation(reading: DirectHeartReading, observedAt: Instant): JSONObject {
        val model = reading.model.take(80)
        val watchConfirmed = model.startsWith("SM-R9", ignoreCase = true) ||
            model.contains("Watch5", ignoreCase = true)
        return JSONObject()
            .put("record_id", "wear-heart-${reading.measuredAt.toEpochMilli()}")
            .put("metric", "vitals.heart_rate")
            .put("record_kind", "series")
            .put("start_at", reading.measuredAt.toString())
            .put("end_at", reading.measuredAt.toString())
            .put("observed_by_companion_at", observedAt.toString())
            .put("upstream_last_modified_at", reading.measuredAt.toString())
            .put("source_package", HeartRateBridgeService.DIRECT_WEAR_PACKAGE)
            .put("recording_method", "active")
            .put("attribution", JSONObject()
                .put("state", if (watchConfirmed) "watch_confirmed" else "external_device")
                .put("evidence", "Direct Wear OS Health Services measurement relayed through the paired phone.")
                .put("device_type", "watch")
                .put("device_model", model.ifBlank { JSONObject.NULL }))
            .put("payload", JSONObject().put("samples", JSONArray().put(
                JSONObject()
                    .put("time", reading.measuredAt.toString())
                    .put("value", reading.bpm)
                    .put("unit", "bpm"),
            )))
    }
}
