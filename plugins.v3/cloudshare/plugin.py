# SPDX-License-Identifier: GPL-3.0-only
"""MoviePilot V3 search, downloader and transfer hooks for CloudShare."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from glob import escape
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from apscheduler.triggers.interval import IntervalTrigger
from app.schemas.download import DOWNLOAD_TASK_ALREADY_EXISTS
from app.schemas.dashboard import DownloaderInfo
from app.schemas.transfer import DownloaderTorrent
from app.sdk.logging import logger
from app.sdk.media import TorrentInfo
from app.sdk.plugin import _PluginBase

from .aliyun import AliyunDiscovery
from .discovery import CloudShareDiscovery, DiscoveryError, SearchResult
from .selection import select_files
from .quarkrobot import TelegramSource
from .inbox import DesktopInbox, EPISODE_NUMBER, GENERIC_VIDEO, VIDEOS
from .quark import Quark, QuarkError
from .queue import JobQueue
from .worker import DESKTOP_ROOT, JobError
from .history import UsageHistory
from .ui import activity_page, defaults as ui_defaults, settings_form


DOWNLOADER = "CloudShare"
TOKEN_KEY = "x.cloudshare"
COOKIE_FILE = Path("/config/cloudshare/cookie")
NAS_MEDIA_ROOT = Path(os.environ.get("CLOUDSHARE_MEDIA_ROOT", "/downloads"))


class CloudShare(_PluginBase):
    plugin_name = "CloudShare"
    plugin_desc = "汇集夸克、阿里云盘与 Telegram 资源，下载到 NAS 并整理入库。"
    plugin_version = "0.2.1"
    plugin_author = "Darin Ji"
    plugin_icon = "Moviepilot_A.png"
    plugin_label = "资源搜索"
    plugin_config_prefix = "cloudshare_"
    plugin_order = 100
    auth_level = 1

    queue = None
    discovery = None
    aliyun = None
    desktop_inbox = None
    desktop_enabled = False
    desktop_progress = None
    quark_enabled = True
    settings = None
    history = None
    history_page = 1

    def init_plugin(self, config=None):
        self.stop_service()
        config = config or {}
        self.settings = {**ui_defaults(NAS_MEDIA_ROOT), **config}
        self.quark_enabled = bool(self.settings["quark_enabled"])
        try:
            limit = max(1, min(5000, int(self.settings["history_limit"])))
        except (TypeError, ValueError):
            limit = 1000
        self.settings["history_limit"] = limit
        try:
            self.history = UsageHistory(Path(self.get_data_path()) / "usage.sqlite3",
                                        enabled=bool(self.settings["history_enabled"]), limit=limit)
        except (OSError, ValueError, sqlite3.Error) as error:
            logger.warning("CloudShare history unavailable (%s)", type(error).__name__)
        self.desktop_enabled = bool(config.get("desktop_enabled"))
        if self.desktop_enabled:
            try:
                self.desktop_inbox = DesktopInbox(
                    Path(config.get("desktop_staging") or NAS_MEDIA_ROOT / "cloudshare-staging"),
                    Path(config.get("desktop_movies") or NAS_MEDIA_ROOT / "quark-inbox"),
                    Path(config.get("desktop_tv") or NAS_MEDIA_ROOT / "quark-tv-inbox"),
                )
            except (OSError, ValueError) as error:
                logger.warning(f"CloudShare desktop inbox configuration: {error}")
        nas = NAS_MEDIA_ROOT.is_dir()
        address = os.environ.get("CLOUDSHARE_PANSOU_URL", "http://pansou:8888")
        root = Path(os.environ.get("CLOUDSHARE_DOWNLOAD_DIR",
                                   str(NAS_MEDIA_ROOT / "cloud") if nas else "/downloads/cloud"))
        try:
            cookie = os.environ.get("CLOUDSHARE_QUARK_COOKIE", "") or self._cookie_from_file()
            parsed = urlsplit(address)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or any((parsed.username, parsed.password, parsed.query, parsed.fragment))
                    or not root.is_absolute() or ".." in root.parts):
                return
            telegram_config = Path("/config/cloudshare/telegram.json")
            telegram = None
            if self.settings["telegram_enabled"] and telegram_config.is_file():
                telegram = TelegramSource(telegram_config, bots={
                    "quark": self.settings["telegram_quark_bot"],
                    "aliyun": self.settings["telegram_aliyun_bot"],
                }, providers={p for p in ("quark", "aliyun") if self.settings[f"telegram_{p}_enabled"]},
                    proxy_url=config.get("telegram_proxy"))
            provider = None
            if cookie:
                try:
                    provider = Quark(cookie)
                except QuarkError:
                    logger.warning("CloudShare Quark credentials are invalid")
            self.queue = JobQueue(
                self.get_data_path(), root, address, provider, telegram=telegram,
                aliyun_auto=bool(config.get("aliyun_auto")) and
                Path("/config/cloudshare/aliyun/cloudshare.json").is_file(), history=self.history)
            self.discovery = CloudShareDiscovery(address, telegram=telegram) if provider is not None else None
            if config.get("aliyun_enabled", True) and self.desktop_inbox is not None:
                self.aliyun = AliyunDiscovery(
                    address, telegram=telegram.for_provider("aliyun") if telegram is not None else None)
        except (OSError, ValueError, QuarkError) as error:
            logger.warning("CloudShare initialization failed (%s)", type(error).__name__)

    @staticmethod
    def _cookie_from_file():
        try:
            descriptor = os.open(COOKIE_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return ""
        with os.fdopen(descriptor) as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 16384:
                raise ValueError("Invalid CloudShare credential file")
            return handle.read(16385).strip()

    def get_state(self):
        return self.queue is not None or self.desktop_inbox is not None

    def get_api(self):
        return [{"path": "/history_page", "endpoint": self.set_history_page,
                 "methods": ["GET"], "auth": "bear", "summary": "Select a CloudShare history page"}]

    def set_history_page(self, page: int = 1):
        count = len(self.history.recent()) if self.history is not None else 0
        self.history_page = max(1, min(max(1, (count + 19) // 20), page))
        return {"success": True, "message": "", "data": {"page": self.history_page}}

    def get_form(self):
        return settings_form(NAS_MEDIA_ROOT, telegram_ready=Path("/config/cloudshare/telegram.json").is_file())

    def get_service(self):
        if self.desktop_inbox is None:
            return []
        return [{
            "id": "CloudShare.DesktopInbox",
            "name": "CloudShare 网盘 SMB 入库",
            "trigger": IntervalTrigger(seconds=30),
            "func": self.scan_desktop_inbox,
        }]

    def scan_desktop_inbox(self):
        inbox = self.desktop_inbox
        if inbox is not None:
            queue = self.queue
            jobs = queue.snapshot() if queue is not None else {}
            managed = [(key, job) for key, job in jobs.items()
                       if job.get("identity_import") and job.get("files")]
            inbox.scan(jobs.values(), managed=managed, on_ready=self._complete_desktop_import)
            self._sample_desktop_progress(inbox.staging, managed)
            if queue is not None:
                self._reconcile_legacy_desktop(inbox, queue, jobs)
                self._publish_desktop_dispatch(inbox.staging, queue, queue.snapshot())

    def _sample_desktop_progress(self, staging, managed):
        now = time.monotonic()
        previous = self.desktop_progress or {}
        samples = {}
        for key, job in managed:
            if job["state"] != "saved":
                continue
            directory = staging / key if job.get("provider") == "aliyun" else staging
            received = 0
            for file in job["files"]:
                name = Path(file["path"]).name
                stem, suffix = Path(name).stem, Path(name).suffix
                candidates = [directory / name, directory / f"{name}.part", *directory.glob(
                    f"{escape(stem)}-*{escape(suffix)}.qkdownloading"
                )]
                sizes = []
                for candidate in candidates:
                    try:
                        info = candidate.lstat()
                    except OSError:
                        continue
                    if stat.S_ISREG(info.st_mode):
                        sizes.append(info.st_size)
                received += min(file["size"], max(sizes, default=0))
            last_time, last_size, _ = previous.get(key, (now, received, 0))
            speed = max(0, received - last_size) / (now - last_time) if now > last_time else 0
            samples[key] = now, received, speed
        self.desktop_progress = samples

    @staticmethod
    def _reconcile_legacy_desktop(inbox, queue, jobs):
        from app.db.oper.transferhistory import TransferHistoryOper

        for key, job in jobs.items():
            if (job["state"] != "saved" or not job.get("save_only") or job.get("identity_import")
                    or len(job["files"]) != 1):
                continue
            file = job["files"][0]
            name = Path(file["path"]).name
            if GENERIC_VIDEO.fullmatch(Path(name).stem):
                title = re.sub(r'[\x00-\x1f/\\:*?"<>|]', " ", job["keyword"]).strip(" .")[:60]
                if title:
                    name = f"{title} - {name}"
            for directory in (inbox.movies, inbox.television):
                source = directory / name
                try:
                    if source.resolve() != source or source.stat().st_size != file["size"]:
                        continue
                    record = TransferHistoryOper().get_success_by_src(str(source), storage="local")
                    if record and record.dest and source.samefile(Path(record.dest)):
                        queue.transition([key], {"saved"}, "transferred")
                        break
                except OSError:
                    continue

    @staticmethod
    def _publish_desktop_dispatch(staging, queue, jobs):
        tasks = []
        aliyun_tasks = []
        for key, job in jobs.items():
            if job["state"] != "saved" or not job.get("identity_import"):
                continue
            if job.get("provider") == "aliyun":
                if metadata := job.get("desktop"):
                    aliyun_tasks.append({
                        "id": key, **metadata, "attempt": job.get("dispatch_attempt", 1),
                        "files": [{"name": Path(file["path"]).name, "size": file["size"]}
                                  for file in job["files"]],
                    })
                continue
            journal = queue.worker.state_dir / f"{key}.json"
            try:
                folder = json.loads(journal.read_text()).get("folder")
            except (OSError, ValueError):
                continue
            if not isinstance(folder, str) or not re.fullmatch(r"[0-9a-f]{32}", folder):
                continue
            tasks.append({
                "id": key,
                "folder": job.get("desktop_folder", key),
                "folderID": folder,
                "files": [Path(file["path"]).name for file in job["files"]],
            })
        CloudShare._write_desktop_manifest(staging / ".cloudshare-dispatch.json", tasks)
        CloudShare._write_desktop_manifest(staging / ".cloudshare-aliyun-dispatch.json", aliyun_tasks)

    @staticmethod
    def _write_desktop_manifest(target, tasks):
        content = json.dumps(tasks, ensure_ascii=False)
        try:
            if target.is_file() and not target.is_symlink() and target.read_text() == content:
                return
            with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, prefix=".cloudshare-",
                                             delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    stream.write(content)
                    stream.flush()
                    os.fchmod(stream.fileno(), 0o644)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
        except OSError as error:
            logger.warning("CloudShare desktop dispatch unavailable (%s)", type(error).__name__)

    def _complete_desktop_import(self, key, job, staged):
        from app.db.oper.downloadhistory import DownloadHistoryOper
        from app.schemas.types import MediaType

        queue = self.queue
        if queue is None:
            return
        destination = queue.worker.download_dir / key
        if destination.resolve() != destination:
            return
        ready = []
        journal = queue.worker.state_dir / f"{key}.json"
        checksums = {} if job.get("provider") == "aliyun" else json.loads(journal.read_text()).get("checksums", {})
        for index, file in enumerate(job["files"]):
            name = Path(file["path"]).name
            target = destination / name
            if target.resolve() != target:
                return
            options = [(path, identity) for path, identity, eligible in staged.get(name, ()) if eligible]
            if len(options) > 1:
                queue.transition([key], {"saved"}, "review")
                return
            if options:
                source, identity = options[0]
                if DesktopInbox._identity(source) != identity:
                    return
                ready.append((source, target, file["size"], checksums.get(str(index))))
            elif not target.is_file() or target.stat().st_size != file["size"]:
                return
        history = DownloadHistoryOper().get_by_hash(key)
        videos = [file for file in job["files"] if Path(file["path"]).suffix.lower() in VIDEOS]
        if (not history or history.downloader != DOWNLOADER
                or not history.media_source or not history.media_id
                or (history.type == MediaType.MOVIE.value and len(videos) != 1)
                or (history.type == MediaType.TV.value and
                    not all(EPISODE_NUMBER.search(Path(file["path"]).name) for file in videos))
                or history.type not in {MediaType.MOVIE.value, MediaType.TV.value}):
            queue.transition([key], {"saved"}, "review")
            return
        try:
            for source, target, size, checksum in ready:
                queue.worker._verify(source, size, checksum)
        except JobError:
            queue.transition([key], {"saved"}, "review")
            return
        destination.mkdir(parents=True, exist_ok=True)
        for source, target, _, _ in ready:
            try:
                os.link(source, target, follow_symlinks=False)
            except FileExistsError:
                if not target.samefile(source):
                    raise
        descriptor = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        for source, target, _, _ in ready:
            if target.samefile(source):
                source.unlink()
        queue.transition([key], {"saved", "review"}, "completed")

    def get_page(self):
        return activity_page(self.settings or ui_defaults(NAS_MEDIA_ROOT), self.history,
                             self.queue.snapshot() if self.queue is not None else {}, self.desktop_progress,
                             telegram_ready=Path("/config/cloudshare/telegram.json").is_file(), page=self.history_page)

    def stop_service(self):
        self.desktop_inbox = None
        self.desktop_enabled = False
        self.desktop_progress = None
        if self.queue is not None:
            self.queue.close()
        self.queue = self.discovery = self.aliyun = None
        self.history = None

    def get_module(self):
        return {name: getattr(self, name) for name in (
            "search_torrents", "async_search_torrents", "download", "list_torrents",
            "start_torrents", "stop_torrents", "remove_torrents", "downloader_info",
            "transfer_completed",
        )} if self.get_state() else {}

    def search_torrents(self, site=None, keyword="", mtype=None, page=0, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, self.async_search_torrents(
                site=site, keyword=keyword, mtype=mtype, page=page, **kwargs,
            )).result()

    async def async_search_torrents(self, site=None, keyword="", mtype=None, page=0, **kwargs):
        discovery = self.discovery
        keyword = str(keyword or "").strip()
        if not 0 < len(keyword) <= 256 or (page or 0) > 0:
            return []
        async def search(source):
            try:
                return await source.search(keyword), ""
            except DiscoveryError as error:
                return [], type(error).__name__

        sources = [("quark", discovery)] if self.quark_enabled and discovery is not None else []
        if self.aliyun is not None:
            sources.append(("aliyun", self.aliyun))
        results = await asyncio.gather(*(search(source) for _, source in sources))
        torrents = []
        for (provider, _), (matches, error) in zip(sources, results):
            if self.history is not None:
                self.history.record("search", provider, keyword,
                                    "error" if error else "done" if matches else "empty",
                                    count=len(matches), detail=error)
            for result in matches:
                candidate = result.candidate
                aliyun = provider == "aliyun"
                digest = self._identity(f"aliyun:{candidate.share_id}" if aliyun else candidate.share_id, ())
                token = urlencode({"xt": "urn:btih:" + digest, TOKEN_KEY: "1",
                                   "share": candidate.share_id, "kw": keyword, "provider": provider})
                if aliyun:
                    description = ("自动转存到阿里云盘后，派发官方客户端写入 NAS，再由 MoviePilot 整理。"
                                   if self.queue is not None and self.queue.aliyun_auto else
                                   "先登记下载，再打开阿里云盘分享。请按下载管理显示的任务目录保存所选文件。")
                    if candidate.passcode:
                        description += f" 提取码 {candidate.passcode}"
                else:
                    description = ("点下载后先转存到夸克网盘。到下载管理查看任务和夸克目录。"
                                   "文件写入 NAS 后由 MoviePilot 整理。" if self.desktop_enabled else "")
                torrents.append(TorrentInfo(
                    title=candidate.title, site_name="阿里云盘" if aliyun else "夸克网盘",
                    site_downloader=DOWNLOADER, description=description,
                    enclosure="magnet:?" + token, page_url=candidate.url, size=result.size,
                ))
        return torrents

    @staticmethod
    def _identity(share_id, episodes):
        return hashlib.sha1(f"cloudshare:v1:{share_id}:{','.join(map(str, episodes))}".encode()).hexdigest()

    def download(self, content, download_dir, cookie="", episodes=None, category=None,
                 label=None, downloader=None, **kwargs):
        if isinstance(content, bytes):
            try:
                content = content.decode()
            except UnicodeDecodeError:
                return None
        if not isinstance(content, str) or not content.startswith("magnet:?"):
            return None
        fields = parse_qs(content[8:])
        if TOKEN_KEY not in fields:
            return None
        queue = self.queue
        if queue is None:
            return DOWNLOADER, None, None, "CloudShare 未启用"
        share = fields.get("share", [""])[0]
        keyword = fields.get("kw", [""])[0]
        provider = fields.get("provider", ["quark"])[0]
        identity = f"aliyun:{share}" if provider == "aliyun" else share
        if provider not in {"aliyun", "quark"}:
            return DOWNLOADER, None, None, "CloudShare 资源渠道无效"
        if (fields[TOKEN_KEY] != ["1"] or not re.fullmatch(r"[A-Za-z0-9]{1,64}", share)
                or not 0 < len(keyword) <= 256
                or fields.get("xt") != ["urn:btih:" + self._identity(identity, ())]):
            return DOWNLOADER, None, None, "CloudShare 资源标识无效"
        if provider == "quark" and not self.quark_enabled:
            return DOWNLOADER, None, None, "夸克渠道已关闭，请在 CloudShare 设置中启用"
        try:
            numbers = sorted(set(episodes or ()))
            if any(type(number) is not int or number < 0 for number in numbers):
                raise ValueError
        except (TypeError, ValueError):
            return DOWNLOADER, None, None, "CloudShare 集数无效"
        digest = self._identity(identity, numbers)
        aliyun_result = None
        if provider == "aliyun":
            if self.aliyun is None or self.desktop_inbox is None:
                return DOWNLOADER, None, None, "请启用阿里云盘渠道和 SMB 入库"
            if digest not in queue.snapshot():
                with ThreadPoolExecutor(max_workers=1) as pool:
                    try:
                        matches = pool.submit(asyncio.run, self.aliyun.search(keyword)).result()
                    except DiscoveryError:
                        matches = []
                match = next((item for item in matches if item.candidate.share_id == share), None)
                files = tuple(select_files(match.files, set(numbers) or None)) if match else ()
                if not files or len({Path(file.path).name for file in files}) != len(files):
                    return DOWNLOADER, None, None, "阿里云盘分享不可用或文件名重复"
                aliyun_result = SearchResult(match.candidate, files)
        save_only = self.desktop_enabled
        if save_only and self.desktop_inbox is None:
            return DOWNLOADER, None, None, "请检查 CloudShare 的 SMB 入库目录设置"
        if save_only and provider == "quark":
            digest = hashlib.sha1(f"desktop:{digest}".encode()).hexdigest()
        try:
            if aliyun_result is not None:
                staging = self.desktop_inbox.staging / digest
                if staging.resolve() != staging:
                    return DOWNLOADER, None, None, "阿里云盘任务目录无效"
                if not queue.aliyun_auto:
                    staging.mkdir(exist_ok=True)
            added = queue.add(digest, share, keyword, numbers, save_only=save_only,
                              identity_import=save_only, aliyun_result=aliyun_result)
        except OSError:
            return DOWNLOADER, None, None, "CloudShare 无法保存任务"
        return DOWNLOADER, digest, None, "" if added else DOWNLOAD_TASK_ALREADY_EXISTS

    @staticmethod
    def _hashes(hashs):
        return {hashs} if isinstance(hashs, str) else set(hashs or ())

    def list_torrents(self, status=None, hashs=None, downloader=None, include_all_tags=False):
        queue = self.queue
        if queue is None or downloader not in (None, DOWNLOADER):
            return None
        requested = self._hashes(hashs)
        status = str(getattr(status, "value", status) or "").casefold()
        tasks = []
        for key, job in queue.snapshot().items():
            state = job["state"]
            if requested and key not in requested:
                continue
            if not requested:
                if status in {"可转移", "transfer"} and state != "completed":
                    continue
                if status in {"下载中", "downloading"} and state in {"completed", "transferred"}:
                    continue
                if status == "completed" and state not in {"completed", "transferred"}:
                    continue
                if status in {"paused", "暂停", "已暂停"} and state not in {"paused", "waiting"}:
                    continue
            size, _, progress, speed = queue.metrics(key, job)
            path = queue.worker.download_dir / key
            title = job.get("title") or job["keyword"]
            desktop = job.get("save_only", False)
            aliyun = job.get("provider") == "aliyun"
            cloud_folder = (f"https://www.alipan.com/s/{job['share']}" if aliyun
                            else f"/{DESKTOP_ROOT}/{job.get('desktop_folder', key)}")
            staged = 0
            if state == "saved" and job.get("identity_import"):
                _, staged, speed = (self.desktop_progress or {}).get(key, (0, 0, 0))
                progress = min(99.9, staged * 100 / size) if size else 0
            if state == "saved":
                next_step = "等待 Mac 下载" if job.get("identity_import") else "请在夸克下载"
                if staged:
                    next_step = "Mac 正在写入 NAS" if speed else "NAS 已收到部分或全部文件"
                if aliyun and job.get("desktop") and not staged:
                    next_step = "已转存，等待官方客户端写入 NAS"
                title = (f"阿里云盘 · 请下载到 {self.desktop_inbox.staging.name if self.desktop_inbox else 'SMB'}/{key}"
                         f" · {title} · {cloud_folder}" if aliyun and not staged
                         and not job.get("desktop")
                         else f"{'阿里云盘' if aliyun else '已转存'} · {next_step} · {title} · {cloud_folder}")
            elif desktop and state in {"pending", "running"}:
                title = f"{title} · 正在转存到{'阿里云盘' if aliyun else '夸克'}"
            elif desktop and state in {"failed", "waiting"}:
                title = f"{title} · {'阿里云盘' if aliyun else '夸克'}转存未完成"
            if state == "transferred":
                title = f"已入库 · {title}"
            if state == "review":
                title = f"待确认媒体或集数 · {title}"
            local_ready = not desktop or (job.get("identity_import") and state in {"completed", "transferred"})
            path_text = str(path) if local_ready else cloud_folder
            tasks.append(DownloaderTorrent(
                downloader=DOWNLOADER, hash=key, title=title, name=title, site_name="阿里云盘" if aliyun else "夸克网盘",
                path=path if local_ready else None, size=size, progress=progress,
                state={"pending": "queued", "running": "downloading", "waiting": "paused",
                       "transferred": "completed", "saved": "downloading" if staged else "paused", "review": "paused"}.get(state, state),
                tags={"transferred": "已整理", "saved": "待下载到 NAS" if aliyun else "已转存，等待夸克客户端下载"}.get(state),
                dlspeed=f"{speed / 1024:.1f} KiB/s" if speed else None,
                save_path=str(path) if job.get("identity_import") else (
                    f"/{DESKTOP_ROOT}" if desktop else str(queue.worker.download_dir)),
                content_path=path_text,
            ))
        return tasks

    def start_torrents(self, hashs, downloader=None):
        if self.queue is not None and downloader in (None, DOWNLOADER):
            keys = self._hashes(hashs)
            saved = {key for key, job in self.queue.snapshot().items()
                     if key in keys and job.get("provider") == "aliyun" and self.queue.aliyun_auto
                     and job["state"] == "saved"}
            resumed = self.queue.transition(saved, {"saved"}, "pending")
            return self.queue.transition(keys - saved, {"paused", "waiting", "failed"}, "pending") or resumed or None
        return None

    def stop_torrents(self, hashs, downloader=None):
        if self.queue is not None and downloader in (None, DOWNLOADER):
            return self.queue.transition(self._hashes(hashs), {"running", "pending"}, "paused") or None
        return None

    def transfer_completed(self, hashs, downloader=None):
        if self.queue is not None and downloader in (None, DOWNLOADER):
            self.queue.transition(self._hashes(hashs), {"completed"}, "transferred")

    def remove_torrents(self, hashs, delete_file=False, downloader=None):
        if self.queue is not None and downloader in (None, DOWNLOADER):
            return self.queue.remove(self._hashes(hashs), delete_file)
        return None

    def downloader_info(self, downloader=None):
        queue = self.queue
        if queue is None or downloader not in (None, DOWNLOADER):
            return None
        size = speed = 0
        for key, job in queue.snapshot().items():
            _, downloaded, _, rate = queue.metrics(key, job)
            if job["state"] == "saved" and job.get("identity_import"):
                _, downloaded, rate = (self.desktop_progress or {}).get(key, (0, 0, 0))
            size += downloaded
            speed += rate
        root = queue.worker.download_dir
        try:
            free = shutil.disk_usage(root if root.exists() else root.parent).free
        except OSError:
            free = 0
        return [DownloaderInfo(download_size=size, download_speed=speed, free_space=free)]
