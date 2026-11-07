# SPDX-License-Identifier: GPL-3.0-only
"""Check that native page rows survive MoviePilot's default-slot renderer."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from test_discovery import discovery
from cloudshare.history import UsageHistory
from cloudshare.ui import activity_page


def nodes(items):
    for node in items:
        yield node
        yield from nodes(node.get("content", []))


class PageTests(unittest.TestCase):
    def test_native_table_contains_explicit_rows_for_the_selected_history_page(self):
        with TemporaryDirectory() as directory:
            history = UsageHistory(Path(directory) / "usage.sqlite3")
            for i in range(21):
                history.record("search", "quark", f"Movie {i}", "done", count=1)
            page = activity_page({}, history, {}, {}, page=2)
            rendered = list(nodes(page))
            titles = [node.get("text") for node in rendered if node["component"] == "td"]
            self.assertIn("Movie 0", titles)
            self.assertNotIn("Movie 20", titles)
            self.assertTrue(any(node["component"] == "VTable" for node in rendered))
            self.assertTrue(any(node.get("events", {}).get("click") for node in rendered))
