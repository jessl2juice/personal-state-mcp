package ai.clinicianassist.personalstate

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action !in SUPPORTED_ACTIONS) return
        if (!LiveHeartService.shouldResume(context)) return
        if (!LiveHeartService.hasLivePermission(context)) return
        if (!LiveHeartService.hasBackgroundPermission(context)) return
        LiveHeartService.start(context)
    }

    companion object {
        private val SUPPORTED_ACTIONS = setOf(
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED,
        )
    }
}
