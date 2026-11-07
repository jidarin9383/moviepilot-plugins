# SPDX-License-Identifier: GPL-3.0-only
"""Exercise the real MoviePilot V3 schema and CloudShare module hooks."""

import asyncio
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import AsyncMock

from test_discovery import discovery
from test_worker import Provider


@unittest.skipUnless(find_spec("app"), "MoviePilot runtime required")
class HostTests(unittest.TestCase):
    def test_history_navigation_uses_the_web_login_authentication(self):
        from cloudshare import CloudShare

        route, = CloudShare.__new__(CloudShare).get_api()
        self.assertEqual(route.get("auth"), "bear")
        self.assertNotIn("allow_anonymous", route)
        self.assertEqual(CloudShare.__new__(CloudShare).set_history_page(500),
                         {"success": True, "message": "", "data": {"page": 1}})

    def test_disabling_quark_preserves_aliyun_search_and_rejects_new_quark_downloads(self):
        from cloudshare import CloudShare
        from cloudshare.aliyun import AliyunCandidate
        from cloudshare.queue import JobQueue

        with TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = CloudShare.__new__(CloudShare)
            plugin.quark_enabled = False
            plugin.queue = JobQueue(root / "state", root / "downloads", "http://example", Provider())
            self.addCleanup(plugin.stop_service)
            plugin.discovery = discovery.CloudShareDiscovery("http://example")
            plugin.discovery.search = AsyncMock(return_value=[])
            plugin.aliyun = AsyncMock()
            plugin.aliyun.search.return_value = [discovery.SearchResult(
                AliyunCandidate("ali", "Movie", ""), (discovery.ShareFile("v", "Movie.mkv", 80_000_000),))]
            matches = asyncio.run(plugin.async_search_torrents(keyword="Movie"))
            self.assertEqual([x.site_name for x in matches], ["阿里云盘"])
            plugin.discovery.search.assert_not_called()
            from urllib.parse import urlencode
            token = "magnet:?" + urlencode({"xt": "urn:btih:" + plugin._identity("share", ()),
                                           "x.cloudshare": "1", "share": "share", "kw": "Movie"})
            self.assertIn("已关闭", plugin.download(token, root)[3])

    def test_aliyun_background_save_emits_only_an_aliyun_dispatch(self):
        import json
        from types import SimpleNamespace
        from unittest.mock import patch

        from cloudshare import CloudShare
        from cloudshare.queue import JobQueue

        with TemporaryDirectory() as directory:
            root = Path(directory)
            queue = JobQueue(root / "state", root / "downloads", "http://example", Provider())
            try:
                queue.aliyun_auto = True
                key = "c" * 40
                job = {"keyword": "Test", "share": "share", "provider": "aliyun",
                       "state": "running", "save_only": True, "identity_import": True,
                       "files": [{"file_id": "source", "path": "clip.MOV", "size": 5,
                                  "parent_id": "root"}]}
                queue.jobs[key] = job
                queue.aliyun_discovery.candidates = AsyncMock(
                    return_value=[discovery.Candidate("share", "Test", "sample-code")])
                metadata = {"userId": "user", "driveId": "drive", "folderID": "d" * 40}
                process = SimpleNamespace(returncode=0, stdout=json.dumps(metadata))
                with patch("cloudshare.queue.subprocess.run", return_value=process):
                    queue._run(key)
                self.assertEqual(queue.snapshot()[key]["state"], "saved")
                CloudShare._publish_desktop_dispatch(root, queue, queue.snapshot())
                self.assertEqual(json.loads((root / ".cloudshare-dispatch.json").read_text()), [])
                task, = json.loads((root / ".cloudshare-aliyun-dispatch.json").read_text())
                self.assertEqual(task["folderID"], metadata["folderID"])
                self.assertEqual(task["files"], [{"name": "clip.MOV", "size": 5}])
                self.assertNotIn("sample-code", (root / "state" / "queue.json").read_text())
            finally:
                queue.close()

    def test_aliyun_partial_download_reports_progress_without_becoming_transferable(self):
        from unittest.mock import patch

        from cloudshare import CloudShare
        from cloudshare.inbox import DesktopInbox
        from cloudshare.queue import JobQueue

        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("staging", "movies", "tv"):
                (root / name).mkdir()
            plugin = CloudShare.__new__(CloudShare)
            plugin.desktop_enabled = True
            plugin.desktop_inbox = DesktopInbox(root / "staging", root / "movies", root / "tv")
            queue = plugin.queue = JobQueue(root / "state", root / "downloads", "http://example", Provider())
            try:
                key = "b" * 40
                job = {"keyword": "Test", "share": "sample", "provider": "aliyun",
                       "state": "saved", "save_only": True, "identity_import": True,
                       "files": [{"path": "sample.MOV", "size": 100}]}
                queue.jobs[key] = job
                destination = root / "staging" / key
                destination.mkdir()
                partial = destination / "sample.MOV.part"
                partial.write_bytes(b"x" * 10)
                with patch("cloudshare.plugin.time.monotonic", return_value=100):
                    plugin._sample_desktop_progress(root / "staging", [(key, job)])
                self.assertEqual(plugin.desktop_progress[key][1], 10)
                partial.write_bytes(b"x" * 30)
                with patch("cloudshare.plugin.time.monotonic", return_value=102):
                    plugin._sample_desktop_progress(root / "staging", [(key, job)])
                task, = plugin.list_torrents(hashs=key)
                self.assertEqual((task.progress, task.state), (30.0, "downloading"))
                self.assertEqual(plugin.desktop_progress[key][2], 10.0)
                self.assertEqual(plugin.list_torrents(status="transfer"), [])
                self.assertEqual(plugin.list_torrents(status="completed"), [])
                info, = plugin.downloader_info("CloudShare")
                self.assertEqual((info.download_size, info.download_speed), (30, 10))
            finally:
                plugin.stop_service()

    def test_search_submission_download_and_transfer_projection(self):
        from app.schemas.download import DOWNLOAD_TASK_ALREADY_EXISTS
        from app.schemas.types import TorrentQueryStatus
        from cloudshare import CloudShare
        from cloudshare.queue import JobQueue

        plugin = CloudShare.__new__(CloudShare)
        provider = Provider()
        candidate = discovery.Candidate("example", "Movie", "fake-passcode")
        selected = discovery.ShareFile("source", "Movie.mkv", len(provider.data))
        search_result = discovery.SearchResult(candidate, (selected,))
        self.assertIs(CloudShare, vars(__import__("cloudshare"))["CloudShare"])
        self.assertFalse(CloudShare.__abstractmethods__)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plugin.queue = JobQueue(root / "state", root / "downloads", "http://example", provider)
            self.addCleanup(plugin.stop_service)
            try:
                plugin.discovery = discovery.CloudShareDiscovery("http://example")
                plugin.discovery.search = AsyncMock(return_value=(search_result,))
                plugin.queue.discovery.candidates = AsyncMock(return_value=[candidate])
                plugin.queue.discovery.resolve = AsyncMock(return_value=search_result)
                torrent, = asyncio.run(plugin.async_search_torrents(keyword="Movie"))
                self.assertEqual(torrent.site_downloader, "CloudShare")
                self.assertNotIn("fake-passcode", torrent.enclosure)
                self.assertIsNone(plugin.download("magnet:?xt=urn:btih:abc", root))
                name, job, _, error = plugin.download(torrent.enclosure, root)
                self.assertEqual((name, error), ("CloudShare", ""))
                self.assertEqual(plugin.download(torrent.enclosure, root)[3],
                                 DOWNLOAD_TASK_ALREADY_EXISTS)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    completed = plugin.list_torrents(status=TorrentQueryStatus.TRANSFER)
                    if completed:
                        break
                    time.sleep(0.05)
                task, = completed
                self.assertEqual((task.hash, task.progress), (job, 100.0))
                self.assertEqual((task.path / "Movie.mkv").read_bytes(), provider.data)
                self.assertIsNone(plugin.list_torrents(hashs=job, downloader="qb-public"))
                plugin.transfer_completed(job, downloader="qb-public")
                self.assertEqual(len(plugin.list_torrents(status=TorrentQueryStatus.TRANSFER)), 1)
                plugin.transfer_completed(job, downloader="CloudShare")
                self.assertEqual(plugin.list_torrents(status=TorrentQueryStatus.TRANSFER), [])
                self.assertEqual(plugin.list_torrents(hashs=job)[0].tags, "已整理")
                self.assertNotIn("fake-passcode", (root / "state" / "queue.json").read_text())
                self.assertEqual(plugin.downloader_info("CloudShare")[0].download_size,
                                 len(provider.data))
                self.assertIsNone(plugin.remove_torrents(job, True, "qb-public"))
                unrelated = task.path / "keep.txt"
                unrelated.write_text("keep")
                self.assertTrue(plugin.remove_torrents(job, True, "CloudShare"))
                self.assertFalse((task.path / "Movie.mkv").exists())
                self.assertEqual(unrelated.read_text(), "keep")
                self.assertEqual(plugin.list_torrents(hashs=job), [])
            finally:
                plugin.stop_service()

    def test_desktop_handoff_exposes_complete_file_under_original_task_hash(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        from cloudshare import CloudShare
        from cloudshare.inbox import DesktopInbox
        from cloudshare.queue import JobQueue

        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("staging", "movies", "tv"):
                (root / name).mkdir()
            plugin = CloudShare.__new__(CloudShare)
            plugin.desktop_enabled = True
            plugin.desktop_inbox = DesktopInbox(root / "staging", root / "movies", root / "tv")
            queue = plugin.queue = JobQueue(root / "state", root / "downloads", "http://example", Provider())
            try:
                key = "a" * 40
                queue.jobs[key] = {"keyword": "Robot Dreams", "state": "saved", "save_only": True,
                                   "identity_import": True, "files": [{"path": "untitled.mkv", "size": 5}]}
                queue.worker.state_dir.mkdir(parents=True)
                (queue.worker.state_dir / f"{key}.json").write_text("{}")
                staged = root / "staging" / "untitled.mkv"
                staged.write_bytes(b"video")
                history = SimpleNamespace(downloader="CloudShare", media_source="douban",
                                          media_id="35426925", type="电影")
                with patch("app.db.oper.downloadhistory.DownloadHistoryOper.get_by_hash", return_value=history):
                    plugin.scan_desktop_inbox()
                    self.assertEqual(plugin.list_torrents(status="transfer"), [])
                    plugin.scan_desktop_inbox()
                completed, = plugin.list_torrents(status="transfer")
                self.assertEqual(completed.hash, key)
                self.assertEqual((completed.path / "untitled.mkv").read_bytes(), b"video")
                self.assertFalse(staged.exists())
            finally:
                plugin.stop_service()
