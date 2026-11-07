# SPDX-License-Identifier: GPL-3.0-only
"""Verify worker recovery and remote deletion guards."""

import hashlib
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from test_discovery import discovery
from cloudshare.quark import QuarkError
from cloudshare.worker import CloudShareWorker, JobError, JobWaiting


class Stream(BytesIO):
    status = 206

    def __init__(self, offset, data, end=None):
        self.headers = {"Content-Range": f"bytes {offset}-{len(data) - 1}/{len(data)}"}
        super().__init__(data[offset:end])


class Provider:
    data = b"resumable data"

    def __init__(self):
        self.saves = 0
        self.folders = []
        self.offsets = []
        self.deleted = []
        self.rows = []
        self.capacity = None
        self.stream_errors = []

    def create_folder(self, path):
        self.folders.append(path)
        return "folder"

    def children(self, folder):
        return self.rows

    def available_bytes(self):
        return self.capacity

    def prepare_save(self, candidate, file, folder):
        return file

    def save(self, file):
        self.saves += 1
        self.rows = [{"fid": "saved", "file_name": Path(file.path).name,
                      "size": file.size, "md5": hashlib.md5(self.data).hexdigest()}]

    def stream(self, fid, offset):
        self.offsets.append(offset)
        if self.stream_errors:
            error = self.stream_errors.pop(0)
            if error is not None:
                raise error
            return Stream(offset, self.data, 4)
        return Stream(offset, self.data)

    def recycle(self, fid):
        self.deleted.append(fid)
        self.rows.clear()


class WorkerTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.provider = Provider()
        self.job = "a" * 32
        self.candidate = discovery.Candidate("share", "Movie", "test-passcode")
        self.files = [discovery.ShareFile("source", "folder/movie.mkv", len(Provider.data),
                                          "parent")]

    def worker(self):
        return CloudShareWorker(self.provider, self.root / "state", self.root / "downloads",
                                sleep=lambda _: None)

    def run_job(self):
        return self.worker().run(self.job, self.candidate, self.files)

    def test_desktop_mode_keeps_quark_copy_and_never_streams(self):
        worker = self.worker()
        folder = f"Movie - {self.job}"
        worker.run(self.job, self.candidate, self.files, save_only=True, desktop_folder=folder)
        worker.run(self.job, self.candidate, self.files, save_only=True, desktop_folder=folder)
        self.assertEqual(self.provider.folders, [f"/MoviePilot-CloudShare/{folder}"])
        self.assertEqual(self.provider.saves, 1)
        self.assertEqual(self.provider.rows[0]["file_name"], "movie.mkv")
        self.assertEqual(self.provider.offsets, [])
        self.assertEqual(self.provider.deleted, [])
        self.assertFalse((self.root / "downloads").exists())

    def test_restart_resumes_partial_and_refreshes_expired_link(self):
        self.provider.stream_errors = [None, QuarkError("request", 401)]
        with self.assertRaises(JobWaiting):
            self.run_job()
        self.assertEqual(self.provider.offsets, [0, 4])
        self.assertEqual(self.provider.deleted, [])
        state = (self.root / "state" / f"{self.job}.json").read_text()
        self.assertNotIn("test-passcode", state)

        self.provider.stream_errors = [QuarkError("request", 403)]
        path = self.run_job()
        self.assertEqual((path / "folder/movie.mkv").read_bytes(), Provider.data)
        self.assertEqual(self.provider.offsets, [0, 4, 4, 4])
        self.assertEqual(self.run_job(), path)
        self.assertEqual(self.provider.saves, 1)
        self.assertEqual(self.provider.deleted, ["saved"])

    def test_capacity_wait_does_not_save(self):
        self.provider.capacity = len(Provider.data)
        with self.assertRaisesRegex(JobWaiting, "capacity"):
            self.run_job()
        self.assertEqual(self.provider.saves, 0)
        self.provider.capacity = None
        self.run_job()
        self.assertEqual(self.provider.saves, 1)

    def test_refuses_recycle_when_folder_contains_unrelated_file(self):
        original = self.provider.stream

        def stream(fid, offset):
            self.provider.rows.append({"fid": "unrelated"})
            return original(fid, offset)

        self.provider.stream = stream
        with self.assertRaisesRegex(JobError, "changed before recycling"):
            self.run_job()
        self.assertEqual(self.provider.deleted, [])

    def test_save_uses_fresh_file_token_from_the_same_share_session(self):
        from cloudshare.quark import Quark

        class FakeQuark(Quark):
            def _api(inner, endpoint, *, params=None, body=None):
                if endpoint.endswith("/token"):
                    return {"stoken": "fresh-session"}
                if endpoint.endswith("/detail"):
                    self.assertEqual(params["stoken"], "fresh-session")
                    self.assertEqual(params["pdir_fid"], "parent")
                    return {"list": [{"fid": "source", "file_name": "movie.mkv",
                                      "size": len(Provider.data), "share_fid_token": "fresh-file"}]}
                self.assertEqual(endpoint, "share/sharepage/save")
                self.assertEqual(body["stoken"], "fresh-session")
                self.assertEqual(body["fid_token_list"], ["fresh-file"])
                return {"task_id": "submitted"}

        provider = FakeQuark("example=value")
        request = provider.prepare_save(self.candidate, self.files[0], "folder")
        self.assertEqual(provider.save(request), "submitted")
