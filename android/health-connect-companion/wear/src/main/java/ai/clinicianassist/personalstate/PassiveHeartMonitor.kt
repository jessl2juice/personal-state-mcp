package ai.clinicianassist.personalstate

import android.content.Context
import android.os.SystemClock
import androidx.health.services.client.HealthServices
import androidx.health.services.client.PassiveListenerService
import androidx.health.services.client.data.DataPointContainer
import androidx.health.services.client.data.DataType
import androidx.health.services.client.data.PassiveListenerConfig
import com.google.common.util.concurrent.FutureCallback
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.MoreExecutors
import java.time.Instant
import kotlin.math.roundToInt

object PassiveHeartMonitor {
    fun register(context: Context) {
        if (!LiveHeartService.hasLivePermission(context) ||
            !LiveHeartService.hasBackgroundPermission(context)
        ) {
            LiveHeartService.writeSharedStatus(context, "Heart permissions are required")
            return
        }
        val config = PassiveListenerConfig.builder()
            .setDataTypes(setOf(DataType.HEART_RATE_BPM))
            .build()
        val future = HealthServices.getClient(context)
            .passiveMonitoringClient
            .setPassiveListenerServiceAsync(PassiveHeartService::class.java, config)
        Futures.addCallback(
            future,
            object : FutureCallback<Void> {
                override fun onSuccess(result: Void?) {
                    LiveHeartService.writeSharedStatus(context, "Battery-safe all-day monitoring active")
                }

                override fun onFailure(throwable: Throwable) {
                    LiveHeartService.writeSharedStatus(context, "Battery-safe monitoring unavailable")
                }
            },
            MoreExecutors.directExecutor(),
        )
    }
}

class PassiveHeartService : PassiveListenerService() {
    override fun onNewDataPointsReceived(dataPoints: DataPointContainer) {
        val sample = dataPoints.getData(DataType.HEART_RATE_BPM).lastOrNull() ?: return
        val bpm = sample.value
        if (!bpm.isFinite() || bpm !in 20.0..240.0) return
        val bootInstant = Instant.now().minusMillis(SystemClock.elapsedRealtime())
        val measuredAt = sample.getTimeInstant(bootInstant)
        LiveHeartService.recordSharedSample(
            this,
            bpm.roundToInt(),
            measuredAt.toEpochMilli(),
            "Battery-safe heart sample",
        )
        HeartRateRelay.send(this, bpm, measuredAt, urgent = false) { status ->
            LiveHeartService.writeSharedStatus(this, status)
        }
    }

    override fun onPermissionLost() {
        LiveHeartService.writeSharedStatus(this, "Heart permissions were removed")
    }
}
