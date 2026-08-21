import unittest

from archive_manager.ingestion.watch_archive import Handler


class WatcherPauseTest(unittest.TestCase):
    def test_pause_toggle_changes_state(self):
        handler = Handler(max_workers=1)
        try:
            self.assertFalse(handler._paused)
            self.assertTrue(handler.toggle_paused())
            self.assertFalse(handler.toggle_paused())
        finally:
            handler._executor.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()