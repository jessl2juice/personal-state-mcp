from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from personal_state_mcp.takeout_search import database_status, index_inputs, search_database


class TakeoutSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "search.db"
        self.archive = self.root / "takeout.zip"
        with zipfile.ZipFile(self.archive, "w") as output:
            output.writestr(
                "Takeout/Google Chat/Groups/clinical-notes.json",
                '{"message":"Cardiology follow-up discussed resting heart rate."}',
            )
            output.writestr("Takeout/Drive/Reports/Quarterly_Budget.pdf", b"%PDF-not-parsed")
            output.writestr("Takeout/Fit/All Data/heart-rate.json", '{"bpm":72}')
        with zipfile.ZipFile(self.root / "unrelated.zip", "w") as output:
            output.writestr("private.txt", "This file must not be discovered as Takeout data.")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_indexes_text_and_binary_metadata(self) -> None:
        result = index_inputs(self.db, [self.archive])
        self.assertEqual(2, result["documents_indexed"])
        self.assertEqual(1, result["documents_excluded"])
        self.assertEqual(1, len(search_database(self.db, "cardiology")))
        self.assertEqual([], search_database(self.db, "bpm"))
        metadata = search_database(self.db, "Quarterly Budget")
        self.assertEqual(1, len(metadata))
        self.assertEqual("Drive", metadata[0]["service"])

    def test_reindex_is_idempotent(self) -> None:
        first = index_inputs(self.db, [self.root])
        self.assertEqual(1, first["archives_seen"])
        second = index_inputs(self.db, [self.root])
        self.assertEqual(1, second["archives_skipped"])
        status = database_status(self.db)
        self.assertEqual(2, status["chunks"])


if __name__ == "__main__":
    unittest.main()
