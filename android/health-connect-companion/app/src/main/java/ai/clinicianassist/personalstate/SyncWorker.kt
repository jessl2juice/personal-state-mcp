package ai.clinicianassist.personalstate

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.HealthConnectFeatures
import androidx.health.connect.client.permission.HealthPermission
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters

class SyncWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val store = PairingStore(applicationContext)
        if (store.pairing() == null) return Result.success()
        val client = HealthConnectClient.getOrCreate(applicationContext)
        if (client.features.getFeatureStatus(HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_IN_BACKGROUND) != HealthConnectFeatures.FEATURE_STATUS_AVAILABLE) {
            return Result.success()
        }
        val granted = client.permissionController.getGrantedPermissions()
        if (HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND !in granted) return Result.success()
        return runCatching { HealthConnectSync(applicationContext, store, client).sync() }
            .fold(onSuccess = { Result.success() }, onFailure = { Result.retry() })
    }
}
