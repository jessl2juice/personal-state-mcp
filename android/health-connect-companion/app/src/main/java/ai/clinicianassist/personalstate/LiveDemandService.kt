package ai.clinicianassist.personalstate

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.net.URL
import java.time.Instant
import java.util.UUID
import javax.net.ssl.HttpsURLConnection

class LiveDemandService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var pollJob: Job? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startAsForeground()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (pollJob?.isActive != true) pollJob = scope.launch { pollLoop() }
        return START_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private suspend fun pollLoop() {
        val store = PairingStore(this)
        var revision = 0
        while (scope.isActive) {
            val pairing = store.pairing()
            if (pairing == null) {
                stopSelf()
                return
            }
            runCatching { poll(pairing, revision) }
                .onSuccess { response ->
                    revision = response.revision
                    if (response.liveRequested && response.leaseSeconds > 0) {
                        WatchRecoveryMessenger.requestLive(this, response.leaseSeconds)
                        store.setLiveHeartStatus("MCP live-heart lease relayed to watch.")
                    }
                }
                .onFailure {
                    store.setLiveHeartStatus("Live-heart relay reconnecting.")
                    delay(RETRY_MS)
                }
        }
    }

    private fun poll(config: PairingConfig, afterRevision: Int): DemandResponse {
        val uploadUrl = URL(config.endpoint)
        val endpoint = URL(uploadUrl, "/api/watch/live-demand?after=$afterRevision")
        require(endpoint.protocol == "https")
        val timestamp = Instant.now().epochSecond.toString()
        val nonce = UUID.randomUUID().toString()
        val batchId = "live-demand-$nonce"
        val signature = UploadClient.sign(config.deviceSecret, timestamp, nonce, batchId, byteArrayOf())
        val connection = endpoint.openConnection() as HttpsURLConnection
        try {
            connection.requestMethod = "GET"
            connection.connectTimeout = 15_000
            connection.readTimeout = 35_000
            connection.useCaches = false
            connection.setRequestProperty("Cache-Control", "no-store")
            connection.setRequestProperty("CF-Access-Client-Id", config.accessClientId)
            connection.setRequestProperty("CF-Access-Client-Secret", config.accessClientSecret)
            connection.setRequestProperty("X-PSM-Device-Id", config.deviceId)
            connection.setRequestProperty("X-PSM-Timestamp", timestamp)
            connection.setRequestProperty("X-PSM-Nonce", nonce)
            connection.setRequestProperty("X-PSM-Batch-Id", batchId)
            connection.setRequestProperty("X-PSM-Signature", signature)
            val code = connection.responseCode
            check(code in 200..299) { "Live demand HTTP $code" }
            val payload = JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
            return DemandResponse(
                revision = payload.getInt("revision"),
                liveRequested = payload.getBoolean("live_requested"),
                leaseSeconds = payload.getInt("lease_seconds"),
            )
        } finally {
            connection.disconnect()
        }
    }

    private fun createNotificationChannel() {
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Personal State live readiness", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Keeps the phone ready to wake live watch sensing only when Personal State is accessed."
                setShowBadge(false)
            },
        )
    }

    private fun startAsForeground() {
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_info_details)
            .setContentTitle("Personal State ready")
            .setContentText("Live watch sensing starts only while the MCP or dashboard is in use")
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setCategory(Notification.CATEGORY_SERVICE)
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    data class DemandResponse(val revision: Int, val liveRequested: Boolean, val leaseSeconds: Int)

    companion object {
        private const val CHANNEL_ID = "personal_state_live_readiness"
        private const val NOTIFICATION_ID = 42
        private const val RETRY_MS = 5_000L

        fun start(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, LiveDemandService::class.java))
        }
    }
}
