package ai.clinicianassist.personalstate

import android.annotation.SuppressLint
import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.IBinder
import android.os.ParcelUuid
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.util.UUID
import kotlin.math.roundToInt

@SuppressLint("MissingPermission")
class FitbitBleHeartService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var store: PairingStore
    private val uploadClient = UploadClient()
    private var bluetoothGatt: BluetoothGatt? = null
    private var scanning = false
    private var scanTimeoutJob: Job? = null
    private var scanAttempt = 0
    private var scanSeen = 0
    private var scanCandidates = 0

    override fun onCreate() {
        super.onCreate()
        store = PairingStore(this)
        createNotificationChannel()
        startForeground(NOTIFICATION_ID, notification("Searching for Fitbit heart rate..."))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopSelf()
                return START_NOT_STICKY
            }
            else -> startScan()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        stopScan()
        if (hasBluetoothConnectPermission()) {
            runCatching { bluetoothGatt?.disconnect() }
            runCatching { bluetoothGatt?.close() }
        }
        bluetoothGatt = null
        setLiveStatus("Fitbit Bluetooth heart-rate stream stopped.")
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun bluetoothAdapter(): BluetoothAdapter? =
        (getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter

    private fun startScan() {
        val pairing = store.pairing()
        if (pairing == null) {
            setLiveStatus("Pair this phone before starting Fitbit Bluetooth heart rate.")
            stopSelf()
            return
        }
        val adapter = bluetoothAdapter()
        if (adapter == null || !adapter.isEnabled) {
            setLiveStatus("Bluetooth is off or unavailable.")
            stopSelf()
            return
        }
        if (!hasBluetoothScanPermission() || !hasBluetoothConnectPermission()) {
            setLiveStatus("Bluetooth scan/connect permission is required for Fitbit live heart rate.")
            stopSelf()
            return
        }
        if (connectBondedFitbit(adapter)) return
        if (scanning) return
        val scanner = adapter.bluetoothLeScanner
        if (scanner == null) {
            setLiveStatus("Bluetooth LE scanner is unavailable.")
            stopSelf()
            return
        }
        scanning = true
        scanAttempt += 1
        scanSeen = 0
        scanCandidates = 0
        setLiveStatus("Scanning for Fitbit Bluetooth heart rate... attempt $scanAttempt")
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .build()
        scanner.startScan(emptyList<ScanFilter>(), settings, scanCallback)
        scanTimeoutJob?.cancel()
        scanTimeoutJob = scope.launch {
            delay(SCAN_TIMEOUT_MS)
            if (scanning) {
                val message = "Fitbit Bluetooth scan saw $scanSeen nearby BLE devices, $scanCandidates possible Fitbit/heart-rate candidates; retrying."
                stopScan(cancelTimeout = false)
                setLiveStatus(message)
                startScan()
            }
        }
    }

    private fun connectBondedFitbit(adapter: BluetoothAdapter): Boolean {
        if (!hasBluetoothConnectPermission()) return false
        val candidates = adapter.bondedDevices
            .mapNotNull { device ->
                val name = runCatching { device.name }.getOrNull()
                if (isFitbitName(name)) name to device else null
            }
            .sortedBy { (name, _) -> if (name.equals("Google Fitbit Air", ignoreCase = true)) 0 else 1 }
        val candidate = candidates.firstOrNull() ?: return false
        setLiveStatus(
            "Connecting to bonded Fitbit Bluetooth device: ${candidate.first}",
            "Connecting to bonded Fitbit Bluetooth device.",
        )
        connect(candidate.second)
        return true
    }

    private fun stopScan(cancelTimeout: Boolean = true) {
        if (!scanning) return
        if (hasBluetoothScanPermission()) {
            runCatching { bluetoothAdapter()?.bluetoothLeScanner?.stopScan(scanCallback) }
        }
        scanning = false
        if (cancelTimeout) {
            scanTimeoutJob?.cancel()
        }
    }

    private val scanCallback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            scanSeen += 1
            if (!isFitbitHeartRateCandidate(result)) return
            scanCandidates += 1
            stopScan()
            connect(result.device)
        }

        override fun onScanFailed(errorCode: Int) {
            setLiveStatus("Fitbit Bluetooth scan failed: $errorCode")
            stopSelf()
        }
    }

    private fun isFitbitHeartRateCandidate(result: ScanResult): Boolean {
        val serviceUuids = result.scanRecord?.serviceUuids?.map { it.uuid }.orEmpty()
        if (HEART_RATE_SERVICE_UUID in serviceUuids) return true
        val name = result.scanRecord?.deviceName ?: runCatching { result.device.name }.getOrNull()
        return isFitbitName(name)
    }

    private fun isFitbitName(name: String?): Boolean {
        val normalized = name?.lowercase().orEmpty()
        return FITBIT_NAME_MARKERS.any { it in normalized }
    }

    private fun connect(device: BluetoothDevice) {
        if (!hasBluetoothConnectPermission()) return
        val name = runCatching { device.name }.getOrNull() ?: "heart-rate device"
        setLiveStatus("Connecting to Fitbit Bluetooth heart rate: $name", "Connecting to Fitbit Bluetooth heart-rate candidate.")
        bluetoothGatt = device.connectGatt(this, false, gattCallback, BluetoothDevice.TRANSPORT_LE)
    }

    private val gattCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_CONNECTED && hasBluetoothConnectPermission()) {
                setLiveStatus("Fitbit Bluetooth connected. Discovering heart-rate service...")
                gatt.discoverServices()
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                setLiveStatus("Fitbit Bluetooth heart rate disconnected.")
                stopSelf()
            }
        }

        override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) {
            if (!hasBluetoothConnectPermission()) return
            val service = gatt.getService(HEART_RATE_SERVICE_UUID)
            val characteristic = service?.getCharacteristic(HEART_RATE_MEASUREMENT_UUID)
            if (characteristic == null) {
                setLiveStatus("No standard heart-rate characteristic was found.")
                stopSelf()
                return
            }
            gatt.setCharacteristicNotification(characteristic, true)
            val descriptor = characteristic.getDescriptor(CLIENT_CHARACTERISTIC_CONFIG_UUID)
            if (descriptor != null) {
                if (Build.VERSION.SDK_INT >= 33) {
                    gatt.writeDescriptor(descriptor, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
                } else {
                    @Suppress("DEPRECATION")
                    descriptor.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                    @Suppress("DEPRECATION")
                    gatt.writeDescriptor(descriptor)
                }
            }
            setLiveStatus("Fitbit Bluetooth heart rate is listening.")
        }

        @Deprecated("Used on Android 12 and earlier")
        override fun onCharacteristicChanged(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic) {
            handleHeartRatePacket(characteristic.value)
        }

        override fun onCharacteristicChanged(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            value: ByteArray,
        ) {
            handleHeartRatePacket(value)
        }
    }

    private fun handleHeartRatePacket(packet: ByteArray) {
        val measurement = runCatching { parseHeartRate(packet) }.getOrElse {
            setLiveStatus("Fitbit Bluetooth heart-rate packet could not be parsed.")
            return
        }
        val now = Instant.now()
        setLiveStatus(
            "FITBIT HEART RATE\n${measurement.bpm.roundToInt()} bpm · live",
            "Fitbit Bluetooth heart-rate sample received.",
        )
        scope.launch {
            val pairing = store.pairing() ?: return@launch
            val sampleJson = JSONObject().apply {
                put("time", now.toString())
                put("value", measurement.bpm)
                put("unit", "bpm")
                put("sensor_contact", measurement.sensorContact)
                if (measurement.rrIntervalsMs.isNotEmpty()) put("rr_intervals_ms", JSONArray(measurement.rrIntervalsMs))
            }
            val observation = JSONObject().apply {
                put("record_id", "fitbit-ble-${now.toEpochMilli()}-${measurement.bpm.roundToInt()}")
                put("metric", "vitals.heart_rate")
                put("record_kind", "series")
                put("start_at", now.toString())
                put("end_at", now.toString())
                put("observed_by_companion_at", now.toString())
                put("upstream_last_modified_at", now.toString())
                put("source_package", FITBIT_BLE_SOURCE_PACKAGE)
                put("recording_method", "active")
                put("attribution", JSONObject().apply {
                    put("state", "external_device")
                    put("evidence", "Direct Bluetooth LE Heart Rate Service 0x180D measurement from phone companion.")
                    put("device_type", "watch")
                    put("device_model", "Fitbit BLE heart-rate device")
                })
                put("payload", JSONObject().apply {
                    put("samples", JSONArray().put(sampleJson))
                    measurement.energyExpendedKj?.let { put("energy_expended_kj", it) }
                })
            }
            val batch = WatchBatch(
                installationId = store.installationId(),
                changes = listOf(upsert(observation)),
                availability = listOf(
                    AvailabilityReport(
                        metric = "vitals.heart_rate",
                        state = "available",
                        evidence = "Direct Bluetooth LE Heart Rate Service sample received.",
                        checkedAt = now,
                        windowStart = now,
                        windowEnd = now,
                    ),
                ),
                generatedAt = now,
            )
            runCatching { uploadClient.upload(pairing, batch) }
                .onSuccess {
                    store.markHeartReadingUploaded(now.toEpochMilli(), measurement.bpm)
                    setLiveStatus(
                        "FITBIT HEART RATE\n${measurement.bpm.roundToInt()} bpm · uploaded live",
                        "Fitbit Bluetooth heart-rate sample uploaded.",
                    )
                }
                .onFailure {
                    setLiveStatus("Fitbit Bluetooth upload failed; still listening.")
                }
        }
    }

    private fun setLiveStatus(status: String, logStatus: String = status) {
        store.setLiveHeartStatus(status)
        Log.i(TAG, logStatus)
        runCatching {
            getSystemService(NotificationManager::class.java)
                ?.notify(NOTIFICATION_ID, notification(logStatus.take(80)))
        }
    }

    private fun parseHeartRate(packet: ByteArray): BleHeartRateMeasurement {
        require(packet.size >= 2) { "packet too short" }
        val flags = packet[0].toInt()
        var offset = 1
        val bpm = if (flags and 0x01 != 0) {
            require(packet.size >= offset + 2) { "missing uint16 bpm" }
            val value = ((packet[offset + 1].toInt() and 0xff) shl 8) or (packet[offset].toInt() and 0xff)
            offset += 2
            value
        } else {
            val value = packet[offset].toInt() and 0xff
            offset += 1
            value
        }
        val contactSupported = flags and 0x04 != 0
        val sensorContact = if (!contactSupported) {
            "unsupported"
        } else if (flags and 0x02 != 0) {
            "detected"
        } else {
            "not_detected"
        }
        val energy = if (flags and 0x08 != 0) {
            require(packet.size >= offset + 2) { "missing energy" }
            (((packet[offset + 1].toInt() and 0xff) shl 8) or (packet[offset].toInt() and 0xff)).also { offset += 2 }
        } else {
            null
        }
        val rr = mutableListOf<Double>()
        if (flags and 0x10 != 0) {
            require((packet.size - offset) % 2 == 0) { "bad rr length" }
            while (offset < packet.size) {
                val raw = ((packet[offset + 1].toInt() and 0xff) shl 8) or (packet[offset].toInt() and 0xff)
                rr += raw / 1024.0 * 1000.0
                offset += 2
            }
        }
        require(bpm in 0..500) { "bpm out of range" }
        return BleHeartRateMeasurement(bpm.toDouble(), sensorContact, energy, rr)
    }

    private fun hasBluetoothScanPermission(): Boolean =
        Build.VERSION.SDK_INT < 31 ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_SCAN) == PackageManager.PERMISSION_GRANTED

    private fun hasBluetoothConnectPermission(): Boolean =
        Build.VERSION.SDK_INT < 31 ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < 26) return
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Fitbit live heart rate", NotificationManager.IMPORTANCE_LOW),
        )
    }

    private fun notification(text: String): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("Personal State")
            .setContentText(text)
            .setOngoing(true)
            .build()

    companion object {
        const val ACTION_STOP = "ai.clinicianassist.personalstate.FITBIT_BLE_STOP"
        const val FITBIT_BLE_SOURCE_PACKAGE = "bluetooth.le.heart_rate_service"
        private const val TAG = "FitbitBleHeartService"
        private const val CHANNEL_ID = "personal-state-fitbit-ble"
        private const val NOTIFICATION_ID = 4202
        private const val SCAN_TIMEOUT_MS = 15_000L
        private val FITBIT_NAME_MARKERS = listOf("fitbit", "sense", "versa", "charge", "inspire", "ace", "luxe")
        private val HEART_RATE_SERVICE_UUID: UUID = UUID.fromString("0000180d-0000-1000-8000-00805f9b34fb")
        private val HEART_RATE_MEASUREMENT_UUID: UUID = UUID.fromString("00002a37-0000-1000-8000-00805f9b34fb")
        private val CLIENT_CHARACTERISTIC_CONFIG_UUID: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

        fun start(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, FitbitBleHeartService::class.java))
        }

        fun stop(context: Context) {
            context.startService(Intent(context, FitbitBleHeartService::class.java).setAction(ACTION_STOP))
        }
    }
}

data class BleHeartRateMeasurement(
    val bpm: Double,
    val sensorContact: String,
    val energyExpendedKj: Int?,
    val rrIntervalsMs: List<Double>,
)
