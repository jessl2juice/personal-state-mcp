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
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
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
import com.google.common.util.concurrent.FutureCallback
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.MoreExecutors
import java.time.Instant
import kotlin.math.roundToInt

class LiveHeartService : Service(), SensorEventListener {
    private val exerciseClient by lazy { HealthServices.getClient(this).exerciseClient }
    private val sensorManager by lazy { getSystemService(SensorManager::class.java) }
    private val mainExecutor = MoreExecutors.directExecutor()
    private val handler = Handler(Looper.getMainLooper())
    private var startInProgress = false
    private var recoveryInProgress = false
    private var lastSampleEpochMs = 0L
    private var lastSampleElapsedMs = 0L
    private var serviceStartedElapsedMs = 0L
    private var liveUntilElapsedMs = 0L
    private var lastNotificationElapsedMs = 0L
    private var exerciseEndRequested = false
    private var directSensorRegistered = false
    private var directSensorUnavailable = false

    private val retryExercise = Runnable { ensureExercise() }
    private val directSensorFallback = Runnable {
        if (directSensorRegistered && lastSampleElapsedMs == 0L) {
            stopDirectSensor()
            directSensorUnavailable = true
            writeStatus("Direct heart sensor unavailable; using Health Services")
            ensureExercise()
        }
    }
    private val stopWhenLeaseEnds = object : Runnable {
        override fun run() {
            val remaining = liveUntilElapsedMs - SystemClock.elapsedRealtime()
            if (remaining <= 0L) {
                endExerciseAndStop("Battery-safe all-day monitoring active")
            } else {
                handler.postDelayed(this, remaining)
            }
        }
    }
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
            writeStatus("Starting on-demand live heart rate")
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

            val bootInstant = Instant.now().minusMillis(SystemClock.elapsedRealtime())
            val samples = update.latestMetrics.getData(DataType.HEART_RATE_BPM)
                .map { it.value to it.getTimeInstant(bootInstant) }
                .filter { (bpm, _) -> bpm.isFinite() && bpm in 20.0..240.0 }
                .sortedBy { (_, measuredAt) -> measuredAt }
            for ((bpm, measuredAt) in samples) {
                recordLiveSample(bpm, measuredAt)
            }
        }

        override fun onLapSummaryReceived(lapSummary: ExerciseLapSummary) = Unit

        override fun onAvailabilityChanged(dataType: DataType<*, *>, availability: Availability) {
            if (dataType != DataType.HEART_RATE_BPM) return
            val status = when (availability.toString()) {
                "AVAILABLE" -> "On-demand live heart rate active"
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
            endExerciseAndStop("Battery-safe all-day monitoring active")
            return START_NOT_STICKY
        }
        val durationMs = intent?.getLongExtra(EXTRA_LIVE_DURATION_MS, DEFAULT_LIVE_SESSION_MS)
            ?.coerceIn(MIN_LIVE_SESSION_MS, MAX_LIVE_SESSION_MS)
            ?: DEFAULT_LIVE_SESSION_MS
        liveUntilElapsedMs = maxOf(liveUntilElapsedMs, SystemClock.elapsedRealtime() + durationMs)
        preferences().edit().putBoolean(KEY_MONITORING_REQUESTED, true).apply()
        runCatching {
            HealthServices.getClient(this).passiveMonitoringClient.clearPassiveListenerServiceAsync()
        }
        handler.removeCallbacks(stopWhenLeaseEnds)
        handler.post(stopWhenLeaseEnds)
        ensureExercise()
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        stopDirectSensor()
        preferences().edit()
            .putBoolean(KEY_SERVICE_ACTIVE, false)
            .putBoolean(KEY_MONITORING_REQUESTED, false)
            .apply()
        exerciseClient.clearUpdateCallbackAsync(callback)
        if (!exerciseEndRequested) runCatching { exerciseClient.endExerciseAsync() }
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onSensorChanged(event: SensorEvent) {
        if (event.sensor.type != Sensor.TYPE_HEART_RATE || event.values.isEmpty()) return
        if (event.accuracy == SensorManager.SENSOR_STATUS_UNRELIABLE ||
            event.accuracy == SensorManager.SENSOR_STATUS_NO_CONTACT
        ) return
        val bpm = event.values[0].toDouble()
        if (!bpm.isFinite() || bpm !in 20.0..240.0) return
        val measuredAt = Instant.now().minusNanos(SystemClock.elapsedRealtimeNanos() - event.timestamp)
        recordLiveSample(bpm, measuredAt)
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    private fun registerCallback() {
        runCatching { exerciseClient.setUpdateCallback(mainExecutor, callback) }
            .onFailure {
                writeStatus("Health service unavailable; retrying")
                handler.postDelayed({ registerCallback() }, RETRY_MS)
            }
    }

    @SuppressLint("WrongConstant", "RestrictedApi")
    private fun ensureExercise() {
        if (!preferences().getBoolean(KEY_MONITORING_REQUESTED, false)) return
        if (directSensorRegistered) return
        if (!hasLivePermission(this)) {
            writeStatus("Heart-rate permission is required")
            return
        }
        if (!directSensorUnavailable && startDirectSensor()) return
        if (startInProgress) return
        startInProgress = true
        Futures.addCallback(
            exerciseClient.getCurrentExerciseInfoAsync(),
            object : FutureCallback<ExerciseInfo> {
                override fun onSuccess(info: ExerciseInfo?) {
                    startInProgress = false
                    when (info?.exerciseTrackedStatus) {
                        ExerciseTrackedStatus.OWNED_EXERCISE_IN_PROGRESS -> {
                            writeStatus("On-demand live heart rate active")
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
                    writeStatus("On-demand live heart rate active")
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
        if (directSensorRegistered) {
            stopDirectSensor()
            directSensorUnavailable = true
            serviceStartedElapsedMs = SystemClock.elapsedRealtime()
            writeStatus("Direct heart stream stalled; using Health Services")
            ensureExercise()
            return
        }
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

    private fun endExerciseAndStop(finalStatus: String) {
        if (exerciseEndRequested) return
        exerciseEndRequested = true
        handler.removeCallbacksAndMessages(null)
        stopDirectSensor()
        Futures.addCallback(
            exerciseClient.endExerciseAsync(),
            object : FutureCallback<Void> {
                override fun onSuccess(result: Void?) = stopNow(finalStatus)
                override fun onFailure(throwable: Throwable) = stopNow(finalStatus)
            },
            mainExecutor,
        )
    }

    private fun stopNow(finalStatus: String) {
        preferences().edit().putBoolean(KEY_MONITORING_REQUESTED, false).apply()
        writeStatus(finalStatus)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
        PassiveHeartMonitor.register(this)
    }

    private fun sendToPhone(bpm: Double, measuredAt: Instant) {
        HeartRateRelay.send(this, bpm, measuredAt, urgent = true, status = ::writeStatus)
    }

    private fun startDirectSensor(): Boolean {
        val sensor = sensorManager.getDefaultSensor(Sensor.TYPE_HEART_RATE) ?: run {
            directSensorUnavailable = true
            return false
        }
        val registered = sensorManager.registerListener(this, sensor, SensorManager.SENSOR_DELAY_FASTEST)
        if (!registered) {
            directSensorUnavailable = true
            return false
        }
        directSensorRegistered = true
        handler.removeCallbacks(directSensorFallback)
        handler.postDelayed(directSensorFallback, DIRECT_SENSOR_FALLBACK_MS)
        writeStatus("Starting on-demand live heart rate")
        return true
    }

    private fun stopDirectSensor() {
        handler.removeCallbacks(directSensorFallback)
        if (directSensorRegistered) sensorManager.unregisterListener(this)
        directSensorRegistered = false
    }

    private fun recordLiveSample(bpm: Double, measuredAt: Instant) {
        val measuredEpochMs = measuredAt.toEpochMilli()
        if (measuredEpochMs <= lastSampleEpochMs) return
        lastSampleEpochMs = measuredEpochMs
        lastSampleElapsedMs = SystemClock.elapsedRealtime()
        handler.removeCallbacks(directSensorFallback)
        writeSample(bpm.roundToInt(), measuredEpochMs, "Live to phone")
        sendToPhone(bpm, measuredAt)
        if (SystemClock.elapsedRealtime() - lastNotificationElapsedMs >= NOTIFICATION_INTERVAL_MS) {
            lastNotificationElapsedMs = SystemClock.elapsedRealtime()
            updateNotification(bpm.roundToInt())
        }
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
        private const val EXTRA_LIVE_DURATION_MS = "live_duration_ms"
        private const val READ_HEART_RATE = "android.permission.health.READ_HEART_RATE"
        private const val READ_HEALTH_DATA_IN_BACKGROUND = "android.permission.health.READ_HEALTH_DATA_IN_BACKGROUND"
        private const val BODY_SENSORS_BACKGROUND = "android.permission.BODY_SENSORS_BACKGROUND"

        private const val DEFAULT_LIVE_SESSION_MS = 30_000L
        private const val MIN_LIVE_SESSION_MS = 10_000L
        private const val MAX_LIVE_SESSION_MS = 5 * 60_000L
        private const val NOTIFICATION_INTERVAL_MS = 15_000L
        private const val DIRECT_SENSOR_FALLBACK_MS = 8_000L

        fun start(context: Context, durationMs: Long = DEFAULT_LIVE_SESSION_MS) {
            context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit().putBoolean(KEY_MONITORING_REQUESTED, true).apply()
            val intent = Intent(context, LiveHeartService::class.java)
                .setAction(ACTION_START)
                .putExtra(EXTRA_LIVE_DURATION_MS, durationMs)
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
                monitoringRequested = preferences.getBoolean(KEY_MONITORING_REQUESTED, false),
                serviceActive = preferences.getBoolean(KEY_SERVICE_ACTIVE, false),
                lastBpm = lastBpm,
                lastSampleEpochMs = preferences.getLong(KEY_LAST_SAMPLE_EPOCH_MS, 0L),
                status = preferences.getString(KEY_STATUS, "Battery-safe monitoring is ready")
                    ?: "Battery-safe monitoring is ready",
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

        fun writeSharedStatus(context: Context, status: String) {
            context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit().putString(KEY_STATUS, status).apply()
        }

        fun resetToPassive(context: Context) {
            context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit()
                .putBoolean(KEY_MONITORING_REQUESTED, false)
                .putBoolean(KEY_SERVICE_ACTIVE, false)
                .putString(KEY_STATUS, "Battery-safe monitoring is ready")
                .apply()
        }

        fun recordSharedSample(context: Context, bpm: Int, measuredAtEpochMs: Long, status: String) {
            context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit()
                .putInt(KEY_LAST_BPM, bpm)
                .putLong(KEY_LAST_SAMPLE_EPOCH_MS, measuredAtEpochMs)
                .putString(KEY_STATUS, status)
                .apply()
        }
    }
}
