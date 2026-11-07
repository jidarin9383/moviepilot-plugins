# SPDX-License-Identifier: GPL-3.0-only
"""为 CloudShare 选择可下载的媒体文件。"""

from dataclasses import dataclass
from pathlib import PurePosixPath
import re


VIDEO_SUFFIXES = {".mp4", ".mkv", ".avi", ".mov", ".m2ts", ".ts", ".webm", ".m4v", ".wmv", ".rmvb"}
SUBTITLE_SUFFIXES = {".srt", ".ass", ".ssa", ".vtt", ".sup"}
MIN_VIDEO_BYTES = 50_000_000
SAMPLE = re.compile(r"(?:^|[\W_])samples?(?:$|[\W_])", re.IGNORECASE)
EPISODE = re.compile(
    r"(?<![a-z0-9])(?:s\d{1,3})?e(?:p)?(\d{1,3})(?!\d)|第\s*(\d{1,3})\s*[集话]",
    re.IGNORECASE,
)
EPISODE_RANGE = re.compile(r"e(\d{1,3})\s*[-–]\s*e?(\d{1,3})(?!\d)", re.IGNORECASE)


@dataclass(frozen=True)
class ShareFile:
    """保存网盘文件身份及用于本地整理的相对路径。"""

    file_id: str
    path: str
    size: int
    parent_id: str = "0"


def _safe_path(value: str) -> bool:
    """拒绝绝对路径、路径穿越和平台相关的路径分隔符。"""
    return bool(value and not any(c in value for c in ("\\", ":", "\x00"))
                and all(part not in {"", ".", ".."} for part in value.split("/")))


def _episodes(stem: str) -> set[int]:
    """识别常见的集号、连集写法和纯数字文件名。"""
    if re.fullmatch(r"\d{1,3}", stem):
        return {int(stem)}
    episodes = {int(a or b) for a, b in EPISODE.findall(stem)}
    # S01E01E02 省略了第二集的分隔符。
    for match in re.finditer(r"(?:s\d{1,3})?e\d{1,3}((?:e\d{1,3})+)", stem, re.I):
        episodes.update(map(int, re.findall(r"e(\d+)", match[1], re.I)))
    for first, last in EPISODE_RANGE.findall(stem):
        episodes.update(range(int(first), int(last) + 1))
    return episodes


def select_files(files: list[ShareFile], episodes: set[int] | None = None) -> list[ShareFile]:
    """保留符合要求的视频及同目录同名字幕。"""
    safe = [file for file in files if _safe_path(file.path) and file.size > 0
            and not SAMPLE.search(file.path)]
    videos = []
    for file in safe:
        path = PurePosixPath(file.path)
        if path.suffix.lower() not in VIDEO_SUFFIXES or file.size < MIN_VIDEO_BYTES:
            continue
        if episodes is not None:
            numbers = _episodes(path.stem)
            if not numbers or not numbers <= episodes:
                continue
        videos.append(file)

    stems: dict[PurePosixPath, set[str]] = {}
    for video in videos:
        path = PurePosixPath(video.path)
        stems.setdefault(path.parent, set()).add(path.stem.casefold())

    subtitles = []
    for file in safe:
        path = PurePosixPath(file.path)
        if path.suffix.lower() not in SUBTITLE_SUFFIXES:
            continue
        stem = path.stem.casefold()
        if any(stem == base or stem.startswith(base + ".")
               for base in stems.get(path.parent, ())):
            subtitles.append(file)
    return videos + subtitles
