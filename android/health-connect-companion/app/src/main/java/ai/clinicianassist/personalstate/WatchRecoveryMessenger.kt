package ai.clinicianassist.personalstate

import android.content.Context
import com.google.android.gms.wearable.Wearable

object WatchRecoveryMessenger {
    const val PATH = "/personal-state/control/resume-heart/v1"

    fun requestResume(context: Context) {
        Wearable.getNodeClient(context).connectedNodes
            .addOnSuccessListener { nodes ->
                nodes.forEach { node ->
                    Wearable.getMessageClient(context).sendMessage(node.id, PATH, byteArrayOf())
                }
            }
    }
}
