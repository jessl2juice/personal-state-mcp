package ai.clinicianassist.personalstate

import android.Manifest
import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import androidx.core.content.ContextCompat
import androidx.health.services.client.ExerciseUpdateCallback
import androidx.health.services.client.HealthServices
import androidx.health.services.client.data.Availability
import androidx.health.services.client.data.BatchingMode
import androidx.health.services.client.data.DataType
import androidx.health.services.client.data.ExerciseConfig
import androidx.health.services.client.data.ExerciseInfo
import androidx.health.services.client.data.ExerciseLapSummary
import androidx.health.services.client.data.ExerciseTrackedStatus
import androidx.health.services.client.data.ExerciseType
import androidx.health.services.client.data.ExerciseUpdate
import com.google.android.gms.wearable.Wearable
import com.google.android.gms.wearable.PutDataRequest
import com.google.common.util.concurrent.FutureCallback
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.MoreExecutors
import org.json.JSONObject
import java.time.Instant
import kotlin.math.roundToInt

class LiveHeartService : Service() {
    private val exerciseClient by lazy { HealthServices.getClient(this).exerciseClient }
    private val mainExecutor = MoreExecutors.directExecutor()
    private val handler = Handler(Looper.getMainLooper())
    private var startInProgress = false
    private var recoveryInProgress = false
    private var lastSentElapsedMs = 0L
    private var lastSampleEpochMs = 0L
    private var lastSampleElapsedMs = 0L
    private var serviceStartedElapsedMs = 0L

    private val retryExercise = Runnable { ensureExercise() }
    private val streamWatchdog = object : Runnable {
        override fun run() {
            val reference = maxOf(lastSampleElapsedMs, serviceStartedElapsedMs)
            if (preferences().getBoolean(KEY_MONITORING_REQUESTED, true) &&
                SystemClock.elapsedRealtime() - reference >= STALE_STREAM_MS
            ) {
                recoverStalledStream()
            }
            handler.postDelayed(this, WATCHDOG_INTERVAL_MS)
        }
    }

    private val callback = object : ExerciseUpdateCallback {
        override fun onRegistered() {
            writeStatus("Starting continuous heart-rate monitoring")
            ensureExercise()
        }

        override fun onRegistrationFailed(throwable: Throwable) {
            writeStatus("Health service unavailable; retrying")
            handler.postDelayed({ registerCallback() }, RETRY_MS)
        }

        override fun onExerciseUpdateReceived(update: ExerciseUpdate) {
            val state = update.exerciseStateInfo.state
            if (state.isEnded) {
                writeStatus("Monitoring paused by the watch; retrying")
                scheduleRetry()
                return
            }

            val sample = update.latestMetrics.getData(DataType.HEART_RATE_BPM).lastOrNull() ?: return
            val bpm = sample.value
            if (!bpm.isFinite() || bpm !in 20.0..240.0) return
            val bootInstant = Instant.now().minusMillis(SystemClock.elapsedRealtime())
            val measuredAt = sample.getTimeInstant(bootInstant)
            val measuredEpochMs = measuredAt.toEpochMilli()
            if (measuredEpochMs <= lastSampleEpochMs) return
            lastSampleEpochMs = measuredEpochMs
            lastSampleElapsedMs = SystemClock.elapsedRealtime()
            writeSample(bpm.roundToInt(), measuredEpochMs, "Live to phone")
            updateNotification(bpm.roundToInt())
            if (SystemClock.elapsedRealtime() - lastSentElapsedMs >= SEND_INTERVAL_MS) {
                lastSentElapsedMs = SystemClock.elapsedRealtime()
                sendToPhone(bpm, measuredAt)
            }
        }

        override fun onLapSummaryReceived(lapSummary: ExerciseLapSummary) = Unit

        override fun onAvailabilityChanged(dataType: DataType<*, *>, availability: Availability) {
            if (dataType != DataType.HEART_RATE_BPM) return
            val status = when (availability.toString()) {
                "AVAILABLE" -> "Continuous heart-rate monitoring active"
                "UNAVAILABLE_DEVICE_OFF_BODY" -> "Sensor needs wrist contact"
                else -> "Heart sensor: $availability"
            }
            writeStatus(status)
        }
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startAsForeground(buildNotification(null))
        serviceStartedElapsedMs = SystemClock.elapsedRealtime()
        preferences().edit().putBoolean(KEY_SERVICE_ACTIVE, true).apply()
        registerCallback()
        handler.postDelayed(streamWatchdog, WATCHDOG_INTERVAL_MS)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            preferences().edit().putBoolean(KEY_MONITORING_REQUESTED, false).apply()
            endExerciseAndStop()
            return START_NOT_STICKY
        }
        preferences().edit().putBoolean(KEY_MONITORING_REQUESTED, true).apply()
        ensureExercise()
        return START_STICKY
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        preferences().edit().putBoolean(KEY_SERVICE_ACTIVE, false).apply()
        exerciseClient.clearUpdateCallbackAsync(callback)
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun registerCallback() {
        runCatching { exerciseClient.setUpdateCallback(mainExecutor, callback) }
            .onFailure {
                writeStatus("Health service unavailable; retrying")
                handler.postDelayed({ registerCallback() }, RETRY_MS)
            }
    }

    @SuppressLint("WrongConstant", "RestrictedApi")
    private fun ensureExercise() {
        if (!preferences().getBoolean(KEY_MONITORING_REQUESTED, true) || startInProgress) return
        if (!hasLivePermission(this)) {
            writeStatus("Heart-rate permission is required")
            return
        }
        startInProgress = true
        Futures.addCallback(
            exerciseClient.getCurrentExerciseInfoAsync(),
            object : FutureCallback<ExerciseInfo> {
                override fun onSuccess(info: ExerciseInfo?) {
                    startInProgress = false
                    when (info?.exerciseTrackedStatus) {
                        ExerciseTrackedStatus.OWNED_EXERCISE_IN_PROGRESS -> {
                            writeStatus("Continuous heart-rate monitoring active")
                        }
                        ExerciseTrackedStatus.OTHER_APP_IN_PROGRESS -> {
                            writeStatus("Another workout is active; monitoring will resume afterward")
                            scheduleRetry()
                        }
                        else -> startExercise()
                    }
                }

                override fun onFailure(throwable: Throwable) {
                    startInProgress = false
                    writeStatus("Could not check heart sensor; retrying")
                    scheduleRetry()
                }
            },
            mainExecutor,
        )
    }

    private fun startExercise() {
        if (startInProgress) return
        startInProgress = true
        val config = ExerciseConfig.builder(ExerciseType.WORKOUT)
            .setDataTypes(setOf(DataType.HEART_RATE_BPM))
            .setIsAutoPauseAndResumeEnabled(false)
            .setIsGpsEnabled(false)
            .setBatchingModeOverrides(setOf(BatchingMode.HEART_RATE_5_SECONDS))
            .build()
        Futures.addCallback(
            exerciseClient.startExerciseAsync(config),
            object : FutureCallback<Void> {
                override fun onSuccess(result: Void?) {
                    startInProgress = false
                    writeStatus("Continuous heart-rate monitoring active")
                }

                override fun onFailure(throwable: Throwable) {
                    startInProgress = false
                    writeStatus("Heart monitoring could not start; retrying")
                    scheduleRetry()
                }
            },
            mainExecutor,
        )
    }

    private fun scheduleRetry() {
        handler.removeCallbacks(retryExercise)
        handler.postDelayed(retryExercise, RETRY_MS)
    }

    @SuppressLint("WrongConstant", "RestrictedApi")
    private fun recoverStalledStream() {
        if (recoveryInProgress || startInProgress) return
        recoveryInProgress = true
        writeStatus("Heart stream stalled; reconnecting")
        Futures.addCallback(
            exerciseClient.getCurrentExerciseInfoAsync(),
            object : FutureCallback<ExerciseInfo> {
                override fun onSuccess(info: ExerciseInfo?) {
                    when (info?.exerciseTrackedStatus) {
                        ExerciseTrackedStatus.OTHER_APP_IN_PROGRESS -> {
                            recoveryInProgress = false
                            serviceStartedElapsedMs = SystemClock.elapsedRealtime()
                            writeStatus("Another workout is active; monitoring will resume afterward")
                        }
                        ExerciseTrackedStatus.OWNED_EXERCISE_IN_PROGRESS -> restartOwnedExercise()
                        else -> resetCallbackAndResume()
                    }
                }

                override fun onFailure(throwable: Throwable) = resetCallbackAndResume()
            },
            mainExecutor,
        )
    }

    private fun restartOwnedExercise() {
        runCatching { exerciseClient.clearUpdateCallbackAsync(callback) }
        Futures.addCallback(
            exerciseClient.endExerciseAsync(),
            object : FutureCallback<Void> {
                override fun onSuccess(result: Void?) = finishRecovery()
                override fun onFailure(throwable: Throwable) = finishRecovery()
            },
            mainExecutor,
        )
    }

    private fun resetCallbackAndResume() {
        runCatching { exerciseClient.clearUpdateCallbackAsync(callback) }
        finishRecovery()
    }

    private fun finishRecovery() {
        startInProgress = false
        recoveryInProgress = false
        serviceStartedElapsedMs = SystemClock.elapsedRealtime()
        handler.postDelayed({ registerCallback() }, RECOVERY_SETTLE_MS)
    }

    private fun endExerciseAndStop() {
        handler.removeCallbacksAndMessages(null)
        Futures.addCallback(
            exerciseClient.endExerciseAsync(),
            object : FutureCallback<Void> {
                override fun onSuccess(result: Void?) = stopNow()
                override fun onFailure(throwable: Throwable) = stopNow()
            },
            mainExecutor,
        )
    }

    private fun stopNow() {
        writeStatus("Monitoring stopped")
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun sendToPhone(bpm: Double, measuredAt: Instant) {
        val payload = JSONObject()
            .put("bpm", bpm)
            .put("measured_at", measuredAt.toString())
            .put("device_model", Build.MODEL)
            .toString()
            .toByteArray(Charsets.UTF_8)

        val dataRequest = PutDataRequest.create(MESSAGE_PATH).apply {
            data = payload
            setUrgent()
        }
        Wearable.getDataClient(this)
            .putDataItem(dataRequest)
            .addOnFailureListener { writeStatus("Live buffered; waiting for phone") }

        Wearable.getNodeClient(this).connectedNodes
            .addOnSuccessListener { nodes ->
                val targets = nodes.filter { it.isNearby }.ifEmpty { nodes }
                if (targets.isEmpty()) {
                    writeStatus("Monitoring active; waiting for phone")
                    return@addOnSuccessListener
                }
                targets.forEach { node ->
                    Wearable.getMessageClient(this)
                        .sendMessage(node.id, MESSAGE_PATH, payload)
                        .addOnSuccessListener { writeStatus("Live to phone") }
                        .addOnFailureListener { writeStatus("Monitoring active; waiting for phone") }
                }
            }
            .addOnFailureListener { writeStatus("Monitoring active; waiting for phone") }
    }

    private fun createNotificationChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                "Live heart monitoring",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Shows when Personal State is collecting live heart rate."
                setShowBadge(false)
            },
        )
    }

    private fun buildNotification(bpm: Int?): Notification {
        val launchIntent = Intent(this, WearMainActivity::class.java)
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_info_details)
            .setContentTitle("Personal State monitoring")
            .setContentText(if (bpm == null) "Starting heart sensor" else "$bpm bpm live to phone")
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setCategory(Notification.CATEGORY_SERVICE)
            .build()
    }

    private fun startAsForeground(notification: Notification) {
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_HEALTH)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun updateNotification(bpm: Int) {
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, buildNotification(bpm))
    }

    private fun writeStatus(status: String) {
        preferences().edit().putString(KEY_STATUS, status).apply()
    }

    private fun writeSample(bpm: Int, measuredAtEpochMs: Long, status: String) {
        preferences().edit()
            .putInt(KEY_LAST_BPM, bpm)
            .putLong(KEY_LAST_SAMPLE_EPOCH_MS, measuredAtEpochMs)
            .putString(KEY_STATUS, status)
            .apply()
    }

    private fun preferences() = getSharedPreferences(PREFS, MODE_PRIVATE)

    data class Snapshot(
        val monitoringRequested: Boolean,
        val serviceActive: Boolean,
        val lastBpm: Int?,
        val lastSampleEpochMs: Long,
        val status: String,
    )

    companion object {
        private const val ACTION_START = "ai.clinicianassist.personalstate.action.START_MONITORING"
        private const val ACTION_STOP = "ai.clinicianassist.personalstate.action.STOP_MONITORING"
        private const val PREFS = "personal_state_live_heart"
        private const val KEY_MONITORING_REQUESTED = "monitoring_requested"
        private const val KEY_SERVICE_ACTIVE = "service_active"
        private const val KEY_LAST_BPM = "last_bpm"
        private const val KEY_LAST_SAMPLE_EPOCH_MS = "last_sample_epoch_ms"
        private const val KEY_STATUS = "status"
        private const val CHANNEL_ID = "personal_state_live_heart"
        private const val NOTIFICATION_ID = 41
        private const val RETRY_MS = 60_000L
        private const val WATCHDOG_INTERVAL_MS = 15_000L
        private const val STALE_STREAM_MS = 45_000L
        private const val RECOVERY_SETTLE_MS = 1_500L
        private const val SEND_INTERVAL_MS = 2_000L
        private const val MESSAGE_PATH = "/personal-state/heart-rate/v1"
        private const val READ_HEART_RATE = "android.permission.health.READ_HEART_RATE"
        private const val READ_HEALTH_DATA_IN_BACKGROUND = "android.permission.health.READ_HEALTH_DATA_IN_BACKGROUND"
        private const val BODY_SENSORS_BACKGROUND = "android.permission.BODY_SENSORS_BACKGROUND"

        fun start(context: Context) {
            context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit().putBoolean(KEY_MONITORING_REQUESTED, true).apply()
            val intent = Intent(context, LiveHeartService::class.java).setAction(ACTION_START)
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit().putBoolean(KEY_MONITORING_REQUESTED, false).apply()
            val intent = Intent(context, LiveHeartService::class.java).setAction(ACTION_STOP)
            context.startService(intent)
        }

        fun snapshot(context: Context): Snapshot {
            val preferences = context.getSharedPreferences(PREFS, MODE_PRIVATE)
            val lastBpm = preferences.getInt(KEY_LAST_BPM, -1).takeIf { it > 0 }
            return Snapshot(
                monitoringRequested = preferences.getBoolean(KEY_MONITORING_REQUESTED, true),
                serviceActive = preferences.getBoolean(KEY_SERVICE_ACTIVE, false),
                lastBpm = lastBpm,
                lastSampleEpochMs = preferences.getLong(KEY_LAST_SAMPLE_EPOCH_MS, 0L),
                status = preferences.getString(KEY_STATUS, "Starting continuous monitoring")
                    ?: "Starting continuous monitoring",
            )
        }

        fun livePermission(): String = if (Build.VERSION.SDK_INT >= 36) READ_HEART_RATE else Manifest.permission.BODY_SENSORS

        fun backgroundPermission(): String = if (Build.VERSION.SDK_INT >= 36) {
            READ_HEALTH_DATA_IN_BACKGROUND
        } else {
            BODY_SENSORS_BACKGROUND
        }

        fun hasLivePermission(context: Context): Boolean =
            context.checkSelfPermission(livePermission()) == PackageManager.PERMISSION_GRANTED

        fun hasBackgroundPermission(context: Context): Boolean = Build.VERSION.SDK_INT < 33 ||
            context.checkSelfPermission(backgroundPermission()) == PackageManager.PERMISSION_GRANTED

        fun shouldResume(context: Context): Boolean =
            context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .getBoolean(KEY_MONITORING_REQUESTED, true)
    }
}
