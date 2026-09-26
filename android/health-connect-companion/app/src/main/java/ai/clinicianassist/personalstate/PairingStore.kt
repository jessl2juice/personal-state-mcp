package ai.clinicianassist.personalstate

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONObject
import java.security.KeyStore
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class PairingConfig(
    val endpoint: String,
    val deviceId: String,
    val deviceSecret: String,
    val accessClientId: String,
    val accessClientSecret: String,
)

class PairingStore(context: Context) {
    private val preferences = context.getSharedPreferences("personal_state_secure", Context.MODE_PRIVATE)
    private val alias = "personal-state-companion-v1"

    fun savePairing(json: String) {
        val parsed = JSONObject(json)
        val expected = setOf("endpoint", "device_id", "device_secret", "cf_access_client_id", "cf_access_client_secret")
        val keys = parsed.keys().asSequence().toSet()
        require(keys == expected) { "Pairing file fields are invalid." }
        val endpoint = parsed.getString("endpoint")
        require(endpoint.startsWith("https://") && endpoint.length <= 512) { "Pairing endpoint must use HTTPS." }
        val config = PairingConfig(
            endpoint = endpoint,
            deviceId = parsed.getString("device_id"),
            deviceSecret = parsed.getString("device_secret"),
            accessClientId = parsed.getString("cf_access_client_id"),
            accessClientSecret = parsed.getString("cf_access_client_secret"),
        )
        require(config.deviceId.length in 8..128)
        require(config.deviceSecret.length in 24..512)
        require(config.accessClientId.length in 8..512)
        require(config.accessClientSecret.length in 8..512)
        putEncrypted("pairing", JSONObject().apply {
            put("endpoint", config.endpoint)
            put("device_id", config.deviceId)
            put("device_secret", config.deviceSecret)
            put("cf_access_client_id", config.accessClientId)
            put("cf_access_client_secret", config.accessClientSecret)
        }.toString())
        if (getEncrypted("installation_id") == null) {
            putEncrypted("installation_id", UUID.randomUUID().toString().replace("-", "_"))
        }
    }

    fun pairing(): PairingConfig? {
        val raw = getEncrypted("pairing") ?: return null
        return runCatching {
            val parsed = JSONObject(raw)
            PairingConfig(
                endpoint = parsed.getString("endpoint"),
                deviceId = parsed.getString("device_id"),
                deviceSecret = parsed.getString("device_secret"),
                accessClientId = parsed.getString("cf_access_client_id"),
                accessClientSecret = parsed.getString("cf_access_client_secret"),
            )
        }.getOrNull()
    }

    fun installationId(): String = getEncrypted("installation_id")
        ?: UUID.randomUUID().toString().replace("-", "_").also { putEncrypted("installation_id", it) }

    fun checkpoint(metric: String): String? = getEncrypted("checkpoint:$metric")

    fun setCheckpoint(metric: String, token: String) = putEncrypted("checkpoint:$metric", token)

    fun clearCheckpoint(metric: String) {
        preferences.edit().remove("checkpoint:$metric").apply()
    }

    fun migrateHeartRateToAllOrigins() {
        val migrationKey = "migration:heart-rate-all-origins-v1"
        if (preferences.getBoolean(migrationKey, false)) return
        preferences.edit()
            .remove("checkpoint:vitals.heart_rate")
            .putBoolean(migrationKey, true)
            .commit()
    }

    fun setStatus(status: String) = putEncrypted("last_status", status.take(512))

    fun status(): String? = getEncrypted("last_status")

    fun setLiveHeartStatus(status: String) = putEncrypted("live_heart_status", status.take(512))

    fun liveHeartStatus(): String? = getEncrypted("live_heart_status")

    fun setPendingHeartReading(payload: String) = putEncrypted("pending_live_heart", payload.take(4096))

    fun pendingHeartReading(): String? = getEncrypted("pending_live_heart")

    fun clearPendingHeartReading(expectedPayload: String) {
        synchronized(PairingStore::class.java) {
            if (getEncrypted("pending_live_heart") == expectedPayload) {
                preferences.edit().remove("pending_live_heart").commit()
            }
        }
    }

    fun lastUploadedHeartEpochMs(): Long = getEncrypted("last_uploaded_live_heart_epoch_ms")
        ?.toLongOrNull()
        ?: 0L

    fun lastUploadedHeartBpm(): Double? = getEncrypted("last_uploaded_live_heart_bpm")
        ?.toDoubleOrNull()

    fun markHeartReadingUploaded(epochMs: Long, bpm: Double) {
        putEncrypted("last_uploaded_live_heart_epoch_ms", epochMs.toString())
        putEncrypted("last_uploaded_live_heart_bpm", bpm.toString())
    }

    fun clear() {
        preferences.edit().clear().apply()
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (keyStore.containsAlias(alias)) keyStore.deleteEntry(alias)
    }

    private fun secretKey(): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val existing = keyStore.getKey(alias, null) as? SecretKey
        if (existing != null) return existing
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(
                alias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build(),
        )
        return generator.generateKey()
    }

    private fun putEncrypted(name: String, value: String) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val encrypted = cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        val packed = ByteArray(cipher.iv.size + encrypted.size)
        cipher.iv.copyInto(packed, 0)
        encrypted.copyInto(packed, cipher.iv.size)
        preferences.edit().putString(name, Base64.encodeToString(packed, Base64.NO_WRAP)).apply()
    }

    private fun getEncrypted(name: String): String? {
        val encoded = preferences.getString(name, null) ?: return null
        return runCatching {
            val packed = Base64.decode(encoded, Base64.NO_WRAP)
            require(packed.size > 12)
            val iv = packed.copyOfRange(0, 12)
            val encrypted = packed.copyOfRange(12, packed.size)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(128, iv))
            String(cipher.doFinal(encrypted), Charsets.UTF_8)
        }.getOrNull()
    }
}
