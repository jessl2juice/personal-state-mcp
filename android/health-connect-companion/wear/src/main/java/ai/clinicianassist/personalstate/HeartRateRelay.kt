package ai.clinicianassist.personalstate

import android.content.Context
import android.os.Build
import com.google.android.gms.wearable.PutDataRequest
import com.google.android.gms.wearable.Wearable
import org.json.JSONObject
import java.time.Instant

object HeartRateRelay {
    const val MESSAGE_PATH = "/personal-state/heart-rate/v1"

    fun send(
        context: Context,
        bpm: Double,
        measuredAt: Instant,
        urgent: Boolean,
        status: (String) -> Unit,
    ) {
        val payload = JSONObject()
            .put("bpm", bpm)
            .put("measured_at", measuredAt.toString())
            .put("device_model", Build.MODEL)
            .toString()
            .toByteArray(Charsets.UTF_8)

        val dataRequest = PutDataRequest.create(MESSAGE_PATH).apply {
            data = payload
            if (urgent) setUrgent()
        }
        Wearable.getDataClient(context)
            .putDataItem(dataRequest)
            .addOnFailureListener { status("Heart data buffered; waiting for phone") }

        Wearable.getNodeClient(context).connectedNodes
            .addOnSuccessListener { nodes ->
                val targets = nodes.filter { it.isNearby }.ifEmpty { nodes }
                if (targets.isEmpty()) {
                    status("Battery-safe monitoring; waiting for phone")
                    return@addOnSuccessListener
                }
                targets.forEach { node ->
                    Wearable.getMessageClient(context)
                        .sendMessage(node.id, MESSAGE_PATH, payload)
                        .addOnSuccessListener {
                            status(if (urgent) "Live to phone" else "Battery-safe heart data synced")
                        }
                        .addOnFailureListener { status("Battery-safe monitoring; waiting for phone") }
                }
            }
            .addOnFailureListener { status("Battery-safe monitoring; waiting for phone") }
    }
}
