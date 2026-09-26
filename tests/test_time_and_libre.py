from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import unittest
from zoneinfo import ZoneInfo

from personal_state_mcp.adapters.libre_linkup import (
    LibreLinkUpClient,
    LibreLinkUpError,
    libreview_account_id_hash,
    map_glucose_item,
    region_host,
    validate_libreview_base_url,
)
from personal_state_mcp.time_utils import parse_vendor_timestamp


class TimeAndLibreTests(unittest.TestCase):
    def test_epoch_timestamp_precedence(self) -> None:
        dt, source = parse_vendor_timestamp(1_779_999_000)
        self.assertEqual(datetime.fromtimestamp(1_779_999_000, timezone.utc), dt)
        self.assertEqual("unix_timestamp", source)

    def test_local_timestamp_uses_configured_timezone(self) -> None:
        dt, source = parse_vendor_timestamp("9/24/2026 1:00:00 PM", ZoneInfo("America/Los_Angeles"))
        self.assertEqual(datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc), dt)
        self.assertEqual("local_timestamp_configured_timezone", source)

    def test_libreview_host_validation(self) -> None:
        self.assertEqual("https://api.libreview.io", validate_libreview_base_url("https://api.libreview.io"))
        self.assertEqual("https://api-fr.libreview.io", region_host("fr"))
        with self.assertRaises(LibreLinkUpError):
            validate_libreview_base_url("http://api.libreview.io")
        with self.assertRaises(LibreLinkUpError):
            validate_libreview_base_url("https://evil.example.com")
        with self.assertRaises(LibreLinkUpError):
            region_host("../evil")

    def test_account_id_header_hash_is_unsalted_libreview_hash(self) -> None:
        account_id = "abc123"
        expected = hashlib.sha256(account_id.encode("utf-8")).hexdigest()
        client = LibreLinkUpClient(email="x@example.com", password="secret")
        client.token = "token"
        client.account_id = account_id
        self.assertEqual(expected, libreview_account_id_hash(account_id))
        self.assertEqual(expected, client._headers(authenticated=True)["Account-Id"])

    def test_map_glucose_item_redacts_raw_payload(self) -> None:
        received = datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)
        mapped = map_glucose_item(
            {
                "ValueInMgPerDl": 101,
                "Timestamp": "2026-09-24T19:58:00Z",
                "TrendArrow": 3,
                "secret": "do-not-store",
            },
            adapter="libre_linkup",
            source_patient_id="patient-1",
            received_at=received,
            stored_at=received,
            sample_type="current",
            selected_region_host="https://api.libreview.io",
            source_timezone=None,
        )
        self.assertEqual(101, mapped.value_mg_dl)
        self.assertNotIn("secret", mapped.raw or {})
        self.assertEqual("libre_linkup", mapped.provenance.adapter)


if __name__ == "__main__":
    unittest.main()

