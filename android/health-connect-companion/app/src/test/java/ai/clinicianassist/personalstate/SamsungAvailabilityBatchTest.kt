package ai.clinicianassist.personalstate

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class SamsungAvailabilityBatchTest {
    @Test
    fun publicBuildReportsAdapterNotInstalledWithoutHealthPayload() {
        val result = kotlinx.coroutines.runBlocking {
            SamsungHealthDataUnavailableAdapter().collect()
        }
        val batch = SamsungAvailabilityBatch(
            installationId = "installation_test_0001",
            identityNamespaceId = "1fa40afd-eb95-4267-a8e8-7941cf751597",
            availability = result.availability,
            generatedAt = Instant.parse("2026-09-26T12:00:00Z"),
            batchId = "25d8b20b-9418-4fc7-9bdc-eb50131071e0",
        ).toJson()

        assertEquals("personal-state-watch-batch/v2", batch.getString("schema_version"))
        assertEquals("android_samsung_health_data", batch.getJSONObject("adapter").getString("id"))
        assertEquals(0, batch.getJSONArray("changes").length())
        assertFalse(batch.getJSONArray("availability").length() == 0)
        assertEquals(
            "adapter_not_installed",
            batch.getJSONArray("availability").getJSONObject(0).getString("state"),
        )
    }
}
