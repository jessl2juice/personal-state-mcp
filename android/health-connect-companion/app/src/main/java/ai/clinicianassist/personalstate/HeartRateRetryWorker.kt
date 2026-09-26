package ai.clinicianassist.personalstate

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.time.Duration

class HeartRateRetryWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val store = PairingStore(applicationContext)
        val pairing = store.pairing() ?: return Result.success()

        repeat(MAX_SAMPLES_PER_RUN) {
            val payload = store.pendingHeartReading() ?: return Result.success()
            val reading = runCatching {
                HeartRatePayload.parse(payload.toByteArray(Charsets.UTF_8), maxAgeSeconds = null)
            }.getOrElse {
                store.clearPendingHeartReading(payload)
                return Result.success()
            }
            if (reading.measuredAt.toEpochMilli() <= store.lastUploadedHeartEpochMs()) {
                store.clearPendingHeartReading(payload)
                return@repeat
            }
            val uploaded = runCatching { LiveHeartUpload.upload(store, pairing, reading) }.isSuccess
            if (!uploaded) return Result.retry()
            store.markHeartReadingUploaded(reading.measuredAt.toEpochMilli(), reading.bpm)
            store.clearPendingHeartReading(payload)
            store.setLiveHeartStatus("Queued watch heart rate uploaded at ${reading.measuredAt}.")
        }
        return if (store.pendingHeartReading() == null) Result.success() else Result.retry()
    }

    companion object {
        private const val WORK_NAME = "personal-state-live-heart-retry"
        private const val MAX_SAMPLES_PER_RUN = 5

        fun enqueue(context: Context) {
            val request = OneTimeWorkRequestBuilder<HeartRateRetryWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, Duration.ofSeconds(10))
                .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                WORK_NAME,
                ExistingWorkPolicy.REPLACE,
                request,
            )
        }
    }
}
