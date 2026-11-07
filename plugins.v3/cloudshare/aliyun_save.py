# SPDX-License-Identifier: GPL-3.0-only
"""Save Alipan files in an isolated process and prepare official-client dispatch."""

import json
import logging
import os
from pathlib import Path, PurePosixPath
import sys
import time

from .discovery import Candidate
from .selection import ShareFile
from .worker import CloudShareWorker, DESKTOP_ROOT, JobError, JobWaiting


AUTH_DIR = Path("/config/cloudshare/aliyun")


class AliyunSaveProvider:
    def __init__(self, client):
        self.client = client

    def create_folder(self, path):
        parts = PurePosixPath(path).parts
        if len(parts) != 3 or parts[0] != "/" or parts[1] != DESKTOP_ROOT:
            raise JobError("Invalid Alipan job directory")
        parent = "root"
        for index, name in enumerate(parts[1:]):
            rows = [row for row in self.client.get_file_list(parent) if row.name == name]
            if len(rows) > 1 or rows and (rows[0].type != "folder" or index == 1):
                raise JobError("Alipan job directory already exists or is ambiguous")
            folder = rows[0] if rows else self.client.create_folder(
                name, parent_file_id=parent, check_name_mode="refuse")
            parent = folder.file_id
        return parent

    def available_bytes(self):
        return None

    def children(self, folder):
        return [{"fid": row.file_id, "file_name": row.name, "size": row.size,
                 "dir": row.type == "folder"} for row in self.client.get_file_list(folder)]

    def prepare_save(self, candidate, file, folder):
        token = self.client.get_share_token(candidate.share_id, candidate.passcode)
        return file, token, folder

    def save(self, request):
        file, token, folder = request
        result = self.client.share_file_saveto_drive(
            file.file_id, token, to_parent_file_id=folder,
            new_name=PurePosixPath(file.path).name, auto_rename=False, overwrite=False)
        if not result.file_id:
            raise JobWaiting("Alipan save outcome is unknown")
        return result.file_id

    def wait(self, file_id):
        for attempt in range(10):
            file = self.client.get_file(file_id)
            if file and file.type == "file":
                return
            time.sleep(1)
        raise JobWaiting("Alipan save has not completed")


def main():
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    try:
        if not (AUTH_DIR / "cloudshare.json").is_file():
            raise JobWaiting("Alipan login required")
        sys.path.insert(0, str(AUTH_DIR / "vendor"))
        from aligo import Aligo, set_config_folder

        set_config_folder(str(AUTH_DIR))
        client = Aligo(name="cloudshare", level=logging.CRITICAL, re_login=False,
                       requests_timeout=10)
        user = client.post("/v2/user/get").json()
        client.default_drive_id = user.get("resource_drive_id") or user["default_drive_id"]
        request = json.load(sys.stdin)
        worker = CloudShareWorker(AliyunSaveProvider(client), request["state_dir"], request["download_dir"])
        candidate = Candidate(request["share"], request["title"], request["passcode"])
        files = [ShareFile(**file) for file in request["files"]]
        worker.run(request["id"], candidate, files, save_only=True)
        journal = json.loads((worker.state_dir / (request["id"] + ".json")).read_text())
        rows = worker.provider.children(journal["folder"])
        expected = {PurePosixPath(file.path).name: file.size for file in files}
        if (len(rows) != len(files) or any(row["dir"] for row in rows)
                or {row["file_name"]: row["size"] for row in rows} != expected):
            raise JobError("Alipan dispatch folder differs from selected files")
        print(json.dumps({"userId": user["user_id"], "driveId": client.default_drive_id,
                          "folderID": journal["folder"]}))
    except Exception as error:
        print(json.dumps({"error": type(error).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
