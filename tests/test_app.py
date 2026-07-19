import tempfile
import unittest
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import catalog as app


class LibraryTests(unittest.TestCase):
    def test_normalize_album(self):
        self.assertEqual(app.normalize("2000 - Hybrid Theory (Deluxe Edition)"), "hybridtheory")

    def test_scan_folder_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Music"
            album = root / "The Band" / "2001 - First Album"
            album.mkdir(parents=True)
            with wave.open(str(album / "01 Song.wav"), "wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000); wav.writeframes(b"\0\0" * 8000)
            with patch.object(app, "DB", Path(temp) / "library.db"):
                result = app.scan_library(root)
                self.assertEqual((result["artists"], result["albums"], result["tracks"]), (1, 1, 1))
                detail = app.artist_data("The Band")
                self.assertEqual(detail["albums"][0]["year"], 2001)
                self.assertEqual(detail["albums"][0]["track_count"], 1)
                dashboard = app.dashboard_data()
                self.assertEqual(dashboard["growth"][-1]["tracks"], 1)
                self.assertEqual(dashboard["growth"][-1]["albums"], 1)

    def test_smart_update_selects_changed_failed_and_stale_artists(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app, "DB", Path(temp) / "library.db"):
            db = app.connect()
            now = datetime.now(timezone.utc)
            artists = [
                ("Current", "current", 1, 10, 100, "2026-01-01", "2026-01-02"),
                ("Changed", "changed", 2, 20, 200, "2026-01-01", "2026-01-03"),
                ("Failed", "failed", 1, 10, 100, "2026-01-01", "2026-01-02"),
                ("Stale", "stale", 1, 10, 100, "2026-01-01", "2026-01-02"),
            ]
            matches = [
                ("Current", "1", "Current", None, 100, "matched", now.isoformat()),
                ("Changed", "2", "Changed", None, 100, "matched", now.isoformat()),
                ("Failed", None, None, None, 0, "not_found", now.isoformat()),
                ("Stale", "4", "Stale", None, 100, "matched", (now - timedelta(days=45)).isoformat()),
            ]
            with db:
                db.executemany("INSERT INTO artists VALUES (?,?,?,?,?,?,?)", artists)
                db.executemany("INSERT INTO artist_matches VALUES (?,?,?,?,?,?,?)", matches)
            before = app._artist_snapshot(db)
            before["Changed"]["album_count"] = 1
            selected = dict(app._smart_update_candidates(db, before, stale_days=30))
            self.assertNotIn("Current", selected)
            self.assertEqual(selected["Changed"], "local collection changed")
            self.assertIn("previous status", selected["Failed"])
            self.assertIn("over 30 days", selected["Stale"])


if __name__ == "__main__":
    unittest.main()
