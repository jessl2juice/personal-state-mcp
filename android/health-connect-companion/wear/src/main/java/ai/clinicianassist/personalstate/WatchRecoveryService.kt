package ai.clinicianassist.personalstate

import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.WearableListenerService
import org.json.JSONObject

class WatchRecoveryService : WearableListenerService() {
    override fun onMessageReceived(messageEvent: MessageEvent) {
        when (messageEvent.path) {
            PASSIVE_PATH -> {
                if (!LiveHeartService.hasLivePermission(this)) return
                if (!LiveHeartService.hasBackgroundPermission(this)) return
                PassiveHeartMonitor.register(this)
            }
            LIVE_PATH -> {
                if (!LiveHeartService.hasLivePermission(this)) return
                val leaseSeconds = runCatching {
                    JSONObject(String(messageEvent.data, Charsets.UTF_8)).getInt("lease_seconds")
                }.getOrDefault(20).coerceIn(10, 60)
                LiveHeartService.start(this, leaseSeconds * 1_000L)
            }
        }
    }

    companion object {
        const val PASSIVE_PATH = "/personal-state/control/passive-heart/v1"
        const val LIVE_PATH = "/personal-state/control/live-heart/v1"
    }
}
