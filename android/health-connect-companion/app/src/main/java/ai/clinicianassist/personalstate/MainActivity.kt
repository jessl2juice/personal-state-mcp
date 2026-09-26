package ai.clinicianassist.personalstate

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.view.WindowManager
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.HealthConnectFeatures
import androidx.health.connect.client.PermissionController
import androidx.health.connect.client.permission.HealthPermission
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import ai.clinicianassist.personalstate.databinding.ActivityMainBinding
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.util.concurrent.TimeUnit
import kotlin.math.max
import kotlin.math.roundToInt

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding
    private lateinit var store: PairingStore
    private val healthClient by lazy { HealthConnectClient.getOrCreate(this) }
    private val samsungAdapter by lazy { SamsungHealthDataAdapterFactory.create(this, store) }

    private val pairingFile = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) return@registerForActivityResult
        lifecycleScope.launch {
            runCatching {
                val bytes = contentResolver.openInputStream(uri)?.use(::readPairingFile)
                    ?: error("Could not read pairing file.")
                store.savePairing(String(bytes, Charsets.UTF_8))
            }.onSuccess {
                binding.connectionStatus.text = "Paired securely. Choose health data, then sync."
            }.onFailure {
                binding.connectionStatus.text = "That pairing file could not be used."
            }
            refreshStatus()
        }
    }

    private val permissionRequest = registerForActivityResult(
        PermissionController.createRequestPermissionResultContract(),
    ) { lifecycleScope.launch { refreshStatus() } }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        store = PairingStore(this)

        binding.pairButton.setOnClickListener {
            pairingFile.launch(arrayOf("application/json", "text/json", "text/plain"))
        }
        binding.permissionButton.setOnClickListener {
            permissionRequest.launch(HealthConnectSync.READ_PERMISSIONS)
        }
        binding.historyPermissionButton.setOnClickListener {
            if (featureAvailable(HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_HISTORY)) {
                permissionRequest.launch(setOf(HealthPermission.PERMISSION_READ_HEALTH_DATA_HISTORY))
            } else {
                binding.permissionSummary.text = "Older-history access is unavailable on this phone."
            }
        }
        binding.backgroundPermissionButton.setOnClickListener {
            if (featureAvailable(HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_IN_BACKGROUND)) {
                permissionRequest.launch(setOf(HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND))
            } else {
                binding.permissionSummary.text = "Background Health Connect access is unavailable on this phone."
            }
        }
        binding.samsungPermissionButton.setOnClickListener {
            lifecycleScope.launch {
                binding.samsungPermissionButton.isEnabled = false
                val result = runCatching { samsungAdapter.requestPermissions(this@MainActivity) }
                binding.permissionSummary.text = result.fold(
                    onSuccess = { it.message },
                    onFailure = { "Samsung Health permission could not be completed: ${it.message ?: "unknown error"}" },
                )
                binding.samsungPermissionButton.isEnabled = true
            }
        }
        binding.syncButton.setOnClickListener { syncNow() }
        lifecycleScope.launch { refreshStatus() }
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) {
                    refreshLiveHeartStatus()
                    delay(1_000)
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        lifecycleScope.launch { refreshStatus() }
    }

    private fun healthConnectReady(): Boolean =
        HealthConnectClient.getSdkStatus(this) == HealthConnectClient.SDK_AVAILABLE

    private fun featureAvailable(feature: Int): Boolean =
        healthConnectReady() && healthClient.features.getFeatureStatus(feature) == HealthConnectFeatures.FEATURE_STATUS_AVAILABLE

    private fun syncNow() {
        if (!healthConnectReady()) {
            binding.connectionStatus.text = "Health Connect is unavailable or needs an update."
            return
        }
        if (store.pairing() == null) {
            binding.connectionStatus.text = "Choose the pairing file first."
            return
        }
        lifecycleScope.launch {
            binding.syncButton.isEnabled = false
            binding.connectionStatus.text = "Reading Health Connect and uploading new records..."
            runCatching { HealthConnectSync(this@MainActivity).sync() }
                .onSuccess { result ->
                    binding.connectionStatus.text = "Sync completed."
                    binding.lastSync.text = result
                    scheduleBackgroundIfAllowed()
                }
                .onFailure {
                    binding.connectionStatus.text = "Sync did not complete. No health payload was queued; the next sync will reread Health Connect."
                }
            binding.syncButton.isEnabled = true
            refreshStatus()
        }
    }

    private suspend fun refreshStatus() {
        val ready = healthConnectReady()
        val pairing = store.pairing()
        val granted = if (ready) healthClient.permissionController.getGrantedPermissions() else emptySet()
        val required = HealthConnectSync.READ_PERMISSIONS.size
        val grantedCount = HealthConnectSync.READ_PERMISSIONS.count { it in granted }
        binding.connectionStatus.text = when {
            !ready -> "Health Connect is unavailable or needs an update."
            pairing == null -> getString(R.string.not_paired)
            grantedCount == 0 -> "Paired. Choose the health data this companion may read."
            grantedCount < required -> "Paired. $grantedCount of $required health categories are allowed."
            else -> "Paired and ready to sync."
        }
        val history = HealthPermission.PERMISSION_READ_HEALTH_DATA_HISTORY in granted
        val background = HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND in granted
        binding.permissionSummary.text = buildString {
            append("Health categories: $grantedCount of $required\n")
            append("Older history: ${if (history) "allowed" else "not allowed"}\n")
            append("Background sync: ${if (background) "allowed" else "not allowed"}")
        }
        binding.lastSync.text = store.status() ?: "No sync has completed."
        binding.samsungPermissionButton.text = if (samsungAdapter.installed) {
            "Allow Samsung Health data"
        } else {
            "Samsung Health reader unavailable"
        }
        binding.samsungPermissionButton.isEnabled = samsungAdapter.installed
        refreshLiveHeartStatus()
        binding.syncButton.isEnabled = ready && pairing != null && (grantedCount > 0 || samsungAdapter.installed)
        if (background) scheduleBackgroundIfAllowed()
    }

    private fun refreshLiveHeartStatus() {
        val pending = store.pendingHeartReading()?.let {
            runCatching { HeartRatePayload.parse(it.toByteArray(), maxAgeSeconds = null) }.getOrNull()
        }
        if (pending != null) {
            binding.liveHeartStatus.text = "WATCH HEART RATE\n${pending.bpm.roundToInt()} bpm · waiting to upload"
            return
        }

        val bpm = store.lastUploadedHeartBpm()
        val measuredAtMs = store.lastUploadedHeartEpochMs()
        if (bpm == null || measuredAtMs <= 0L) {
            binding.liveHeartStatus.text = store.liveHeartStatus() ?: "Direct watch stream has not connected yet."
            return
        }

        val ageSeconds = max(0L, (System.currentTimeMillis() - measuredAtMs) / 1_000L)
        val label = if (ageSeconds <= 60L) "LIVE HEART RATE" else "LAST WATCH HEART RATE"
        val age = when {
            ageSeconds < 60L -> "$ageSeconds sec old"
            ageSeconds < 3_600L -> "${ageSeconds / 60L} min old"
            else -> "${ageSeconds / 3_600L} hr old"
        }
        binding.liveHeartStatus.text = "$label\n${bpm.roundToInt()} bpm · $age"
    }

    private fun scheduleBackgroundIfAllowed() {
        if (!featureAvailable(HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_IN_BACKGROUND)) return
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .setRequiresBatteryNotLow(true)
            .build()
        val request = PeriodicWorkRequestBuilder<SyncWorker>(1, TimeUnit.HOURS)
            .setConstraints(constraints)
            .build()
        WorkManager.getInstance(this).enqueueUniquePeriodicWork(
            "personal-state-health-connect-sync",
            ExistingPeriodicWorkPolicy.KEEP,
            request,
        )
    }

    private fun readPairingFile(input: InputStream): ByteArray {
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(4096)
        while (true) {
            val count = input.read(buffer)
            if (count == -1) break
            require(output.size() + count <= MAX_PAIRING_FILE_BYTES) { "Pairing file is too large." }
            output.write(buffer, 0, count)
        }
        return output.toByteArray()
    }

    companion object {
        private const val MAX_PAIRING_FILE_BYTES = 16 * 1024
    }
}
