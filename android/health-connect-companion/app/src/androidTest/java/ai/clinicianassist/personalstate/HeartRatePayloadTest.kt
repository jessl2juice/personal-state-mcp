package ai.clinicianassist.personalstate

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

class HeartRatePayloadTest {
    @Test
    fun payloadRoundTrips() {
        val reading = DirectHeartReading(83.5, Instant.parse("2026-09-25T20:00:00Z"), "SM-R920")
        val encoded = HeartRatePayload.encode(reading)

        val decoded = HeartRatePayload.parse(
            encoded.toByteArray(),
            now = Instant.parse("2026-09-25T20:00:10Z"),
        )

        assertEquals(reading, decoded)
    }

    @Test
    fun livePayloadRejectsOldReading() {
        val payload = """{"bpm":72,"measured_at":"2026-09-25T19:00:00Z","device_model":"SM-R920"}"""

        try {
            HeartRatePayload.parse(
                payload.toByteArray(),
                now = Instant.parse("2026-09-25T20:00:00Z"),
            )
            fail("Expected old live reading to be rejected")
        } catch (_: IllegalArgumentException) {
            // Expected.
        }
    }
}
