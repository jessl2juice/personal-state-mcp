package ai.clinicianassist.personalstate

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
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
            adapter = result.adapter,
            sourceChanges = result.sourceChanges,
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

    @Test
    fun failedLimitedReadIsExplicitInCoverage() {
        val adapter = org.json.JSONObject()
            .put("id", "android_samsung_health_data")
            .put("version", "1.1.0")
        val batch = SamsungAvailabilityBatch(
            installationId = "installation_test_0001",
            identityNamespaceId = "1fa40afd-eb95-4267-a8e8-7941cf751597",
            adapter = adapter,
            sourceChanges = emptyList(),
            availability = listOf(
                SamsungAdapterAvailability(
                    metric = "sleep.summary",
                    state = "companion_read_failed",
                    evidence = "Samsung Health read failed: DataLimitExceeded.",
                    windowStart = Instant.parse("2026-09-25T12:00:00Z"),
                    windowEnd = Instant.parse("2026-09-26T12:00:00Z"),
                    truncated = true,
                    interrupted = true,
                ),
            ),
            generatedAt = Instant.parse("2026-09-26T12:00:00Z"),
        ).toJson()

        val coverage = batch.getJSONArray("availability").getJSONObject(0).getJSONObject("coverage")
        assertTrue(coverage.getBoolean("truncated"))
        assertTrue(coverage.getBoolean("interrupted"))
        assertFalse(coverage.getBoolean("backfill_limited"))
    }
}
