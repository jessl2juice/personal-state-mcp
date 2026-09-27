package ai.clinicianassist.personalstate

import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.WearableListenerService

class WatchRecoveryService : WearableListenerService() {
    override fun onMessageReceived(messageEvent: MessageEvent) {
        if (messageEvent.path != PATH) return
        if (!LiveHeartService.shouldResume(this)) return
        if (!LiveHeartService.hasLivePermission(this)) return
        if (!LiveHeartService.hasBackgroundPermission(this)) return
        LiveHeartService.start(this)
    }

    companion object {
        const val PATH = "/personal-state/control/resume-heart/v1"
    }
}
