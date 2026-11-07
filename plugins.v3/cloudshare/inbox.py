# SPDX-License-Identifier: GPL-3.0-only
"""Publish complete Quark desktop downloads from the NAS staging directory."""

import os
from pathlib import Path
import re
import stat
from threading import Lock

from app.sdk.logging import logger


VIDEOS = {".mkv", ".mp4", ".m4v", ".mov", ".avi", ".ts", ".m2ts", ".webm"}
SUBTITLES = {".srt", ".ass", ".ssa", ".vtt", ".sup"}
GENERIC_VIDEO = re.compile(r"(?:[0-9]{3,4}[pi]|[248]k|uhd|fhd|hd)", re.IGNORECASE)
EPISODE_NUMBER = re.compile(r"(?i)(?:s\d{1,3}[ ._-]*e\d{1,4}|\d{1,2}x\d{1,3}|第\s*\d+\s*[集话話])")
EPISODE = re.compile(
    r"(?i)(?<![a-z0-9])(?:s\d{1,3}[ ._-]*e\d{1,4}|\d{1,2}x\d{1,3})"
    r"|第\s*\d+\s*[集话話]|(?<![a-z])season[ ._-]*\d+"
)


class DesktopInbox:
    def __init__(self, staging: Path, movies: Path, television: Path):
        self.staging, self.movies, self.television = staging, movies, television
        roots = (staging, movies, television)
        if any(not root.is_dir() or root.resolve() != root for root in roots):
            raise ValueError("Inbox paths must be existing absolute directories")
        if any(a.is_relative_to(b) for i, a in enumerate(roots)
               for j, b in enumerate(roots) if i != j):
            raise ValueError("Inbox directories must not overlap")
        if len({root.stat().st_dev for root in roots}) != 1:
            raise ValueError("Inbox directories must share a filesystem")
        self._previous = {}
        self._lock = Lock()

    def scan(self, jobs=(), *, managed=(), on_ready=None):
        reserved = {}
        aliyun = {key for key, job in managed if job.get("provider") == "aliyun"}
        for key, job in managed:
            for file in job["files"]:
                name = Path(file["path"]).name
                reserved.setdefault((name, file["size"]), []).append(key)

        media_names = {}
        for job in jobs:
            if job.get("state") != "saved" or len(job["files"]) != 1 or job.get("episodes"):
                continue
            file = job["files"][0]
            name = Path(file["path"]).name
            if GENERIC_VIDEO.fullmatch(Path(name).stem):
                title = re.sub(r'[\x00-\x1f/\\:*?"<>|]', " ", job["keyword"]).strip(" .")[:60]
                if title:
                    media_names.setdefault((name, file["size"]), set()).add(title)

        with self._lock:
            seen, stable = {}, {}

            for parent, directories, filenames in os.walk(self.staging):
                directories[:] = [name for name in directories if not name.startswith(".")]
                if any(name.lower().endswith((".qkdownloading", ".part", ".tmp", ".download"))
                       for name in filenames):
                    continue
                for name in sorted(filenames, key=lambda value: Path(value).suffix.lower() not in SUBTITLES):
                    source = Path(parent) / name
                    if name.startswith(".") or source.suffix.lower() not in VIDEOS | SUBTITLES:
                        continue
                    try:
                        identity = self._identity(source)
                        if identity is None:
                            continue
                        if self._previous.get(source) == identity:
                            owners = reserved.get((name, identity[2]))
                            if owners:
                                for key in owners:
                                    stable.setdefault(key, {}).setdefault(name, []).append(
                                        (source, identity, source.is_relative_to(self.staging / key)
                                         or (key not in aliyun and len(owners) == 1)))
                                continue
                            titles = media_names.get((name, identity[2]), ())
                            if len(titles) > 1:
                                continue
                            title = next(iter(titles), "")
                            self._publish(source, identity, title)
                        else:
                            seen[source] = identity
                    except OSError as error:
                        logger.warning(f"CloudShare desktop import kept {name!r}: {error.strerror}")
            for key, job in managed:
                if job["state"] not in {"saved", "review"}:
                    continue
                files = stable.get(key, {})
                if any(not any(eligible for _, _, eligible in options) for options in files.values()):
                    continue
                try:
                    on_ready(key, job, files)
                except OSError as error:
                    logger.warning(f"CloudShare desktop task {key} kept: {error.strerror}")
            self._previous = seen

    @staticmethod
    def _identity(source):
        info = source.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_size:
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
        return None

    def _publish(self, source, identity, title=""):
        relative = source.relative_to(self.staging)
        explicit_tv = len(relative.parts) > 1 and relative.parts[0].casefold() in {"tv", "电视剧", "剧集"}
        root = self.television if explicit_tv or EPISODE.search(str(relative)) else self.movies
        target = root / (Path(*relative.parts[1:]) if explicit_tv else relative)
        if title:
            target = target.with_name(f"{title} - {target.name}")
        if source.resolve() != source or target.resolve() != target:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if self._identity(source) != identity:
            return
        try:
            os.link(source, target, follow_symlinks=False)
        except FileExistsError:
            if self._identity(target) != identity:
                raise
        descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if self._identity(source) == identity and self._identity(target) == identity:
            source.unlink()
            logger.info(f"CloudShare desktop import published {target.name!r}")
