# SPDX-License-Identifier: GPL-3.0-only
"""Verify Alipan save-only jobs through the shared checkpoint worker."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from test_discovery import discovery
from cloudshare.aliyun_save import AliyunSaveProvider
from cloudshare.worker import CloudShareWorker, JobError


class Client:
    def __init__(self):
        self.rows = {"root": []}
        self.saves = 0

    def get_file_list(self, parent):
        return self.rows[parent]

    def create_folder(self, name, *, parent_file_id, check_name_mode):
        assert check_name_mode == "refuse"
        folder = SimpleNamespace(file_id=f"folder{len(self.rows)}", name=name, type="folder", size=0)
        self.rows[parent_file_id].append(folder)
        self.rows[folder.file_id] = []
        return folder

    def get_share_token(self, share, passcode):
        return share, passcode

    def share_file_saveto_drive(self, source, token, *, to_parent_file_id, new_name, auto_rename, overwrite):
        assert token == ("share", "sample-code")
        assert (source, auto_rename, overwrite) == ("source", False, False)
        self.saves += 1
        saved = SimpleNamespace(file_id="saved", name=new_name, size=5, type="file")
        self.rows[to_parent_file_id].append(saved)
        return saved

    def get_file(self, file_id):
        return SimpleNamespace(file_id=file_id, type="file")


class AliyunSaveTests(unittest.TestCase):
    def test_save_only_resumes_without_copying_the_same_file_twice(self):
        client = Client()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worker = CloudShareWorker(AliyunSaveProvider(client), root / "state", root / "downloads")
            candidate = discovery.Candidate("share", "Test", "sample-code")
            files = [discovery.ShareFile("source", "nested/clip.MOV", 5)]
            for _ in range(2):
                worker.run("a" * 40, candidate, files, save_only=True)
            self.assertEqual(client.saves, 1)
            self.assertEqual(client.rows["folder2"][0].name, "clip.MOV")
            self.assertFalse((root / "downloads").exists())
            self.assertNotIn("sample-code", (root / "state" / ("a" * 40 + ".json")).read_text())

    def test_refuses_an_existing_task_folder_without_a_checkpoint(self):
        client = Client()
        provider = AliyunSaveProvider(client)
        provider.create_folder("/MoviePilot-CloudShare/" + "a" * 40)
        with self.assertRaises(JobError):
            provider.create_folder("/MoviePilot-CloudShare/" + "a" * 40)


if __name__ == "__main__":
    unittest.main()
