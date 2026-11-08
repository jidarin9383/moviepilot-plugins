# SPDX-License-Identifier: GPL-3.0-only
"""Run one CloudShare job with durable checkpoints and resumable downloads."""

import fcntl
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path, PurePosixPath
import re
import time
from contextlib import contextmanager

from .quark import QuarkError
from .selection import _safe_path


TEMP_ROOT = "MoviePilot-CloudShare-Temp"
DESKTOP_ROOT = "MoviePilot-CloudShare"


class JobError(Exception):
    """A job could not proceed safely."""


class JobWaiting(JobError):
    """The job is resumable after an external condition changes."""


class CloudShareWorker:
    """Serialize jobs and retain progress independently of the host process."""

    def __init__(self, provider, state_dir, download_dir, *, sleep=time.sleep):
        self.provider = provider
        self.state_dir = Path(state_dir).resolve()
        self.download_dir = Path(download_dir).resolve()
        self.sleep = sleep
        self.last_save = None

    def run(self, job_id, candidate, files, *, check=lambda: None, refresh=None, save_only=False,
            desktop_folder=None):
        if self.provider is None:
            raise JobWaiting("Quark credentials are not configured")
        with self._locked():
            try:
                return self._run(job_id, candidate, tuple(files), check, refresh, save_only, desktop_folder)
            except QuarkError as error:
                if error.status in (401, 429):
                    raise JobWaiting("Quark account needs attention") from None
                raise

    @contextmanager
    def _locked(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with (self.state_dir / "worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise JobWaiting("Another CloudShare job is running") from None
            yield

    def remove(self, job_id, delete_files):
        if not re.fullmatch(r"[0-9a-f]{32,64}", job_id):
            raise JobError("Invalid job")
        with self._locked():
            journal = self.state_dir / f"{job_id}.json"
            if not journal.exists():
                return
            state = json.loads(journal.read_text())
            if state["phase"] in {"creating", "saving"}:
                raise JobWaiting("Resume the job to reconcile its Quark state before removing")
            if saved := state.get("saved"):
                self._recycle_saved(state["folder"], saved)
            base = self.download_dir / job_id
            parts = self.download_dir / ".cloudshare-parts" / job_id
            directories = {base, parts}
            for index, (_, relative, _) in enumerate(state["identity"]):
                if not _safe_path(relative):
                    raise JobError("Invalid recorded path")
                self._local_path(parts, str(index)).unlink(missing_ok=True)
                inode = state.get("published", {}).get(str(index))
                if delete_files and inode:
                    final = self._local_path(base, relative)
                    try:
                        info = final.stat()
                    except FileNotFoundError:
                        continue
                    if [info.st_dev, info.st_ino] != inode:
                        continue
                    final.unlink()
                    directories.update(parent for parent in final.parents if parent.is_relative_to(base))
            for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            journal.unlink()

    def _recycle_saved(self, folder, saved):
        rows = self.provider.children(folder)
        if rows:
            if len(rows) != 1 or rows[0].get("fid") != saved or rows[0].get("dir"):
                raise JobError("Temporary folder changed before recycling")
            self.provider.recycle(saved)

    def _run(self, job_id, candidate, files, check, refresh, save_only, desktop_folder):
        check()
        if not re.fullmatch(r"[0-9a-f]{32,64}", job_id) or not files:
            raise JobError("Invalid job")
        paths = [file.path for file in files]
        if (any(not _safe_path(path) for path in paths) or len(set(paths)) != len(paths)
                or any(type(file.size) is not int or file.size <= 0 for file in files)):
            raise JobError("Invalid file selection")
        if save_only and (len(files) > 100 or len({PurePosixPath(p).name for p in paths}) != len(files)):
            raise JobError("Desktop save needs at most 100 distinct filenames")

        journal = self.state_dir / f"{job_id}.json"
        identity = [[file.file_id, file.path, file.size] for file in files]
        if journal.exists():
            with journal.open() as handle:
                state = json.load(handle)
            if state["identity"] != identity or state["share"] != candidate.share_id:
                raise JobError("Job selection changed")
        else:
            state = {"identity": identity, "share": candidate.share_id, "index": 0,
                     "folder": None, "phase": "new"}

        def commit(**changes):
            state.update(changes)
            temporary = journal.with_suffix(".tmp")
            with temporary.open("w") as handle:
                json.dump(state, handle)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(journal)
            directory_fd = os.open(self.state_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

        if state["index"] == len(files):
            return self.download_dir / job_id
        if state["phase"] == "new":
            commit(phase="creating")
            root = DESKTOP_ROOT if save_only else TEMP_ROOT
            name = desktop_folder if save_only and desktop_folder else job_id
            if not _safe_path(name) or PurePosixPath(name).name != name:
                raise JobError("Invalid Quark folder name")
            folder = self.provider.create_folder(f"/{root}/{name}")
            if not isinstance(folder, str) or not folder or folder == "0":
                raise JobError("Invalid temporary folder identity")
            commit(folder=folder, phase="ready")
        if not state["folder"]:
            raise JobWaiting("Temporary folder creation outcome is unknown")

        while state["index"] < len(files):
            check()
            index = state["index"]
            file = files[index]
            final = self._local_path(self.download_dir / job_id, file.path)
            part = self._local_path(self.download_dir / ".cloudshare-parts" / job_id, str(index))
            folder = state["folder"]

            if state["phase"] == "ready":
                if not save_only and (final.exists() or part.exists() or self.provider.children(folder)):
                    raise JobError("Job destination already contains files")
                if save_only and any(row.get("file_name") == PurePosixPath(file.path).name
                                     for row in self.provider.children(folder)):
                    raise JobError("Quark destination already contains this filename")
                available = self.provider.available_bytes()
                if available is not None and available * 10 < file.size * 11:
                    raise JobWaiting("Insufficient Quark capacity")
                if self.last_save is not None:
                    self.sleep(max(0, 5 - (time.monotonic() - self.last_save)))
                source = refresh() if refresh is not None else candidate
                request = self.provider.prepare_save(source, file, folder)
                check()
                commit(phase="saving")
                self.last_save = time.monotonic()
                task_id = self.provider.save(request)
                commit(task=task_id)
            if state["phase"] == "saving":
                if state.get("task"):
                    self.provider.wait(state["task"])
                rows = self.provider.children(folder)
                if save_only:
                    rows = [row for row in rows if row.get("file_name") == PurePosixPath(file.path).name]
                if not rows:
                    raise JobWaiting("Quark save outcome is unknown")
                row = rows[0]
                saved = row.get("fid")
                if (len(rows) != 1 or not isinstance(saved, str) or saved in {"", "0", folder}
                        or row.get("dir") or row.get("size") != file.size
                        or row.get("file_name") != PurePosixPath(file.path).name):
                    raise JobError("Saved file identity differs from selection")
                checksum = row.get("md5")
                if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", checksum):
                    checksum = None
                if save_only:
                    checksums = {**state.get("checksums", {}), str(index): checksum}
                    commit(index=index + 1, phase="ready", task=None, checksums=checksums)
                    continue
                commit(phase="download", saved=saved, md5=checksum)

            if state["phase"] == "download":
                self._download(state["saved"], file.size, part, check)
                self._verify(part, file.size, state["md5"])
                commit(phase="publish")

            if state["phase"] == "publish":
                if part.exists():
                    self._verify(part, file.size, state["md5"])
                    final.parent.mkdir(parents=True, exist_ok=True)
                    if not final.exists():
                        os.link(part, final)
                    if not final.samefile(part):
                        raise JobError("Download destination was replaced")
                    info = final.stat()
                    published = {**state.get("published", {}), str(index): [info.st_dev, info.st_ino]}
                    commit(published=published)
                    part.unlink()
                else:
                    self._verify(final, file.size, state["md5"])
                commit(phase="recycle")

            if state["phase"] == "recycle":
                self._verify(final, file.size, state["md5"])
                self._recycle_saved(folder, state["saved"])
                commit(index=index + 1, phase="ready", saved=None, md5=None, task=None)
        return self.download_dir / job_id

    def _local_path(self, base, relative):
        path = base / relative
        if path.resolve() != path:
            raise JobError("Download path crosses a symlink")
        return path

    def _download(self, file_id, size, part, check):
        part.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(6):
            check()
            offset = part.stat().st_size if part.exists() else 0
            if offset == size:
                return
            if offset > size:
                raise JobError("Partial file exceeds expected size")
            try:
                with self.provider.stream(file_id, offset) as response:
                    if response.status == 206:
                        expected = f"bytes {offset}-{size - 1}/{size}"
                        if response.headers.get("Content-Range") != expected:
                            raise JobError("Unexpected Content-Range")
                    elif response.status != 200 or offset:
                        raise JobError("Server did not honor resume offset")
                    with part.open("ab") as handle:
                        remaining = size - offset
                        while remaining:
                            check()
                            chunk = response.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise OSError("Incomplete response")
                            handle.write(chunk)
                            remaining -= len(chunk)
                        if response.read(1):
                            handle.truncate(offset)
                            raise JobError("Response exceeds expected size")
                        handle.flush()
                        os.fsync(handle.fileno())
                return
            except QuarkError as error:
                if error.status in (401, 429):
                    raise JobWaiting("Quark account needs attention") from None
                if error.status and error.status < 500 and error.status != 403:
                    raise
            except (OSError, HTTPException):
                pass
            if attempt < 5:
                self.sleep(2 ** attempt)
        raise JobError("Download retries exhausted")

    @staticmethod
    def _verify(path, size, checksum):
        if not path.is_file() or path.stat().st_size != size:
            raise JobError("Downloaded file size differs")
        if checksum:
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "md5").hexdigest()
            if digest != checksum.lower():
                raise JobError("Downloaded file checksum differs")
