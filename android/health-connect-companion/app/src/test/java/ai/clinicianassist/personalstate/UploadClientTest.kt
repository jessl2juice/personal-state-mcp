package ai.clinicianassist.personalstate

import org.junit.Assert.assertEquals
import org.junit.Test

class UploadClientTest {
    @Test
    fun signatureMatchesServerContractVector() {
        val signature = UploadClient.sign(
            secret = "test-device-secret",
            timestamp = "1790294400",
            nonce = "fixed-nonce",
            batchId = "fixed-batch",
            body = "{\"schema_version\":\"personal-state-watch-batch/v1\"}".toByteArray(),
        )

        assertEquals("TvfPDelw8C75HbLKM5rQdPBbUTeXUo9lhK6P1IKIJxw", signature)
    }
}
