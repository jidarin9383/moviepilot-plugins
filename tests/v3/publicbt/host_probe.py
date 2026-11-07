# SPDX-License-Identifier: GPL-3.0-only
"""Run isolated PublicBT compatibility checks with MoviePilot's actual SDK."""

import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[3] / "plugins.v3"), "/app"]
from publicbt import PublicBT
from app.schemas.event import ResourceDownloadEventData
from app.sdk.media import TorrentInfo


class HostTests(unittest.TestCase):
    def test_real_schema_secret_storage_search_and_handoff(self):
        with TemporaryDirectory() as directory:
            class IsolatedPublicBT(PublicBT):
                def get_data_path(self, plugin_id=None):
                    return Path(directory)

                def update_config(self, config, plugin_id=None):
                    self.saved = config
                    return True

            plugin = IsolatedPublicBT.__new__(IsolatedPublicBT)
            plugin.init_plugin({"enabled": True, "prowlarr_url": "http://example.invalid",
                                "api_key": "host-test-placeholder", "downloader": "qb-public",
                                "download_path": "/downloads/bt"})
            self.assertTrue(plugin.get_state())
            self.assertEqual(plugin.saved["api_key"], "")
            response = Mock(status_code=200)
            response.aclose = AsyncMock()
            response.json.return_value = [{"protocol": "torrent", "indexerId": 1, "indexer": "Test",
                "title": "Host.Sample", "size": 100, "seeders": 2,
                "magnetUrl": "magnet:?xt=urn:btih:" + "a" * 40}]
            client = Mock()
            client.get_res = AsyncMock(return_value=response)
            with patch("publicbt.AsyncRequestUtils", return_value=client):
                torrent, = asyncio.run(plugin.async_search_torrents(keyword="Host sample"))
                self.assertIsInstance(torrent, TorrentInfo)
                response.json.return_value = {"version": "2.3.0"}
                self.assertTrue(asyncio.run(plugin.test_connection())["data"]["ok"])
            event = ResourceDownloadEventData(context=SimpleNamespace(torrent_info=torrent),
                                              downloader="qb-public", options={})
            plugin.on_resource_download(SimpleNamespace(event_data=event))
            self.assertFalse(event.cancel)
            self.assertEqual(event.options["save_path"], "/downloads/bt")
            self.assertEqual(plugin._history.recent()[0]["status"], "prepared")
            self.assertNotIn("host-test-placeholder", json.dumps(plugin.get_page()))
            for route in plugin.get_api():
                self.assertEqual(route["auth"], "bear")
            self.assertEqual(set(plugin.set_history_page(500)), {"success", "message", "data"})


if __name__ == "__main__":
    unittest.main()
