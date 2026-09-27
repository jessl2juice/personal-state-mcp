package ai.clinicianassist.personalstate

import android.content.Context
import com.google.android.gms.wearable.Wearable
import org.json.JSONObject

object WatchRecoveryMessenger {
    const val PASSIVE_PATH = "/personal-state/control/passive-heart/v1"
    const val LIVE_PATH = "/personal-state/control/live-heart/v1"

    fun requestPassive(context: Context) {
        Wearable.getNodeClient(context).connectedNodes
            .addOnSuccessListener { nodes ->
                nodes.forEach { node ->
                    Wearable.getMessageClient(context).sendMessage(node.id, PASSIVE_PATH, byteArrayOf())
                }
            }
    }

    fun requestLive(context: Context, leaseSeconds: Int) {
        val payload = JSONObject()
            .put("lease_seconds", leaseSeconds.coerceIn(10, 60))
            .toString()
            .toByteArray(Charsets.UTF_8)
        Wearable.getNodeClient(context).connectedNodes
            .addOnSuccessListener { nodes ->
                nodes.forEach { node ->
                    Wearable.getMessageClient(context).sendMessage(node.id, LIVE_PATH, payload)
                }
            }
    }
}
