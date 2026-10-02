package ai.clinicianassist.personalstate

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.URL
import java.time.Instant
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import javax.net.ssl.HttpsURLConnection

class UploadClient {
    suspend fun upload(config: PairingConfig, batch: UploadBatch): Boolean = withContext(Dispatchers.IO) {
        val body = batch.bytes()
        require(body.size <= 1024 * 1024) { "Batch exceeds the server contract." }
        val endpoint = URL(config.endpoint)
        require(endpoint.protocol == "https") { "Only HTTPS endpoints are accepted." }
        val timestamp = Instant.now().epochSecond.toString()
        val nonce = java.util.UUID.randomUUID().toString()
        val signature = sign(config.deviceSecret, timestamp, nonce, batch.batchId, body)
        val connection = endpoint.openConnection() as HttpsURLConnection
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 15_000
            connection.readTimeout = 30_000
            connection.doOutput = true
            connection.useCaches = false
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setRequestProperty("Cache-Control", "no-store")
            connection.setRequestProperty("CF-Access-Client-Id", config.accessClientId)
            connection.setRequestProperty("CF-Access-Client-Secret", config.accessClientSecret)
            connection.setRequestProperty("X-PSM-Device-Id", config.deviceId)
            connection.setRequestProperty("X-PSM-Timestamp", timestamp)
            connection.setRequestProperty("X-PSM-Nonce", nonce)
            connection.setRequestProperty("X-PSM-Batch-Id", batch.batchId)
            connection.setRequestProperty("X-PSM-Signature", signature)
            connection.setFixedLengthStreamingMode(body.size)
            connection.outputStream.use { it.write(body) }
            val code = connection.responseCode
            if (code !in 200..299) {
                val error = connection.errorStream?.bufferedReader()?.use { it.readText().take(300) } ?: "no response body"
                error("Upload to ${endpoint.host} HTTP $code: $error")
            }
            true
        } finally {
            connection.disconnect()
        }
    }

    companion object {
        fun sign(secret: String, timestamp: String, nonce: String, batchId: String, body: ByteArray): String {
            val prefix = "$timestamp\n$nonce\n$batchId\n".toByteArray(Charsets.UTF_8)
            val message = ByteArray(prefix.size + body.size)
            prefix.copyInto(message, 0)
            body.copyInto(message, prefix.size)
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(secret.toByteArray(Charsets.UTF_8), "HmacSHA256"))
            return Base64.getUrlEncoder().withoutPadding().encodeToString(mac.doFinal(message))
        }
    }
}
