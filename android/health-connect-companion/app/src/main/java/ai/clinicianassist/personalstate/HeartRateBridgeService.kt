package ai.clinicianassist.personalstate

import android.os.Build
import com.google.android.gms.wearable.DataEvent
import com.google.android.gms.wearable.DataEventBuffer
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.WearableListenerService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
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

    @Synchronized
    private fun handlePayload(payload: ByteArray) {
        val store = PairingStore(this)
        val pairing = store.pairing() ?: run {
            store.setLiveHeartStatus("Watch connected, but this phone is not paired for upload.")
            return
        }
        val reading = runCatching { HeartRatePayload.parse(payload) }.getOrElse {
            store.setLiveHeartStatus("Watch sent an invalid heart-rate reading.")
            return
        }
        if (reading.measuredAt.toEpochMilli() <= store.lastUploadedHeartEpochMs()) return

        val pendingPayload = HeartRatePayload.encode(reading)
        store.setPendingHeartReading(pendingPayload)

        // WearableListenerService keeps the process awake only for the callback. Complete the
        // fast path before returning, then leave an encrypted WorkManager retry if it fails.
        runCatching {
            runBlocking(Dispatchers.IO) { LiveHeartUpload.upload(store, pairing, reading) }
        }.onSuccess {
            store.markHeartReadingUploaded(reading.measuredAt.toEpochMilli(), reading.bpm)
            store.clearPendingHeartReading(pendingPayload)
            store.setLiveHeartStatus("Live watch heart rate uploaded at ${reading.measuredAt}.")
        }.onFailure { error ->
            val reason = error.message?.replace(Regex("\\s+"), " ")?.take(180) ?: error::class.simpleName
            store.setLiveHeartStatus("Live watch upload queued after failure: $reason")
            HeartRateRetryWorker.enqueue(this)
        }
    }

    companion object {
        const val MESSAGE_PATH = "/personal-state/heart-rate/v1"
        const val DIRECT_WEAR_PACKAGE = "ai.clinicianassist.personalstate"
    }
}

data class DirectHeartReading(val bpm: Double, val measuredAt: Instant, val model: String)

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
        val observedAt = Instant.now()
        val model = reading.model.take(80)
        val watchConfirmed = model.startsWith("SM-R9", ignoreCase = true) ||
            model.contains("Watch5", ignoreCase = true)
        val observation = JSONObject()
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
        val availability = AvailabilityReport(
            metric = "vitals.heart_rate",
            state = "available",
            evidence = "A direct Wear OS heart-rate sample was received from the paired watch.",
            checkedAt = observedAt,
            windowStart = reading.measuredAt,
            windowEnd = reading.measuredAt,
        )
        val batch = WatchBatch(store.installationId(), listOf(upsert(observation)), listOf(availability))
        check(UploadClient().upload(pairing, batch))
    }
}
