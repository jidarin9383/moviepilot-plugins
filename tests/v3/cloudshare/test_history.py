# SPDX-License-Identifier: GPL-3.0-only
"""Check durable usage history and sensitive-data exclusion."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from test_discovery import discovery
from cloudshare.history import UsageHistory


class HistoryTests(unittest.TestCase):
    def test_history_survives_restart_and_bounds_retention(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "usage.sqlite3"
            history = UsageHistory(path, limit=2)
            for title in ["First", "Second", "Third"]:
                history.record("search", "quark", title, "done", count=3)
            rows = UsageHistory(path, limit=2).recent()
            self.assertEqual([x["title"] for x in rows], ["Third", "Second"])
            self.assertEqual(rows[0]["count"], 3)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_history_removes_share_urls_codes_and_credentials(self):
        with TemporaryDirectory() as directory:
            history = UsageHistory(Path(directory) / "usage.sqlite3")
            history.record("search", "aliyun",
                           "Movie https://www.alipan.com/s/test?pwd=private 提取码: abcd api_hash=secret",
                           "done", detail="SessionError")
            text = str(history.recent())
            for secret in ["https://", "private", "abcd", "secret"]:
                self.assertNotIn(secret, text)
            self.assertIn("Movie", text)

    def test_disabling_recording_preserves_existing_history(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "usage.sqlite3"
            UsageHistory(path).record("search", "quark", "Old", "done")
            history = UsageHistory(path, enabled=False)
            history.record("search", "quark", "New", "done")
            self.assertEqual([x["title"] for x in history.recent()], ["Old"])

    def test_existing_tasks_are_seeded_once_without_inventing_original_times(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "usage.sqlite3"
            jobs = {"a" * 40: {"keyword": "Old movie", "state": "saved"}}
            history = UsageHistory(path)
            history.seed_tasks(jobs)
            UsageHistory(path).seed_tasks(jobs)
            row, = history.recent()
            self.assertEqual((row["kind"], row["detail"]), ("snapshot", "existing_task"))

    def test_queue_records_transitions_and_keeps_deleted_task_history(self):
        from cloudshare.queue import JobQueue
        from test_worker import Provider

        with TemporaryDirectory() as directory:
            root = Path(directory)
            history = UsageHistory(root / "usage.sqlite3")
            queue = JobQueue(root / "state", root / "downloads", "http://example", Provider(), history=history)
            queue.close()
            queue.stop.clear()
            queue.add("a" * 40, "share", "Test movie", [], save_only=True)
            queue.transition(["a" * 40], {"pending"}, "paused")
            queue.remove(["a" * 40], False)
            self.assertEqual([row["status"] for row in history.recent()], ["removed", "paused", "pending"])
            self.assertEqual(queue.snapshot(), {})
