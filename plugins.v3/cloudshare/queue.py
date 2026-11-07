# SPDX-License-Identifier: GPL-3.0-only
"""Persist CloudShare jobs and run them outside MoviePilot request threads."""

import asyncio
from dataclasses import asdict
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from threading import Event, RLock, Thread
import time

from .discovery import Candidate, CloudShareDiscovery, DiscoveryError
from .aliyun import AliyunDiscovery
from .selection import ShareFile
from .worker import CloudShareWorker, JobError, JobWaiting
from .quark import QuarkError


class JobQueue:
    def __init__(self, data_dir, download_dir, pansou_url, provider, *, telegram=None, aliyun_auto=False, history=None):
        self.path = Path(data_dir) / "queue.json"
        self.worker = CloudShareWorker(provider, Path(data_dir) / "checkpoints", download_dir)
        self.discovery = CloudShareDiscovery(pansou_url, telegram=telegram)
        self.aliyun_discovery = AliyunDiscovery(
            pansou_url, telegram=telegram.for_provider("aliyun") if telegram is not None else None)
        self.aliyun_auto = aliyun_auto
        self.lock = RLock()
        self.wake = Event()
        self.stop = Event()
        self.speed_samples = {}
        if self.path.exists():
            self.jobs = json.loads(self.path.read_text())
        else:
            self.jobs = {}
        self.history = history
        self._last_history = {key: dict(job) for key, job in self.jobs.items()}
        if history is not None:
            history.seed_tasks(self.jobs)
        for job in self.jobs.values():
            if job["state"] == "running":
                job["state"] = "pending"
        self.thread = Thread(target=self._loop, name="cloudshare-download", daemon=True)
        self.thread.start()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as handle:
            json.dump(self.jobs, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)
        if self.history is not None:
            for key in self._last_history.keys() | self.jobs.keys():
                job = self.jobs.get(key)
                previous = self._last_history.get(key)
                if job is not None and (previous is None or previous["state"] != job["state"]):
                    self.history.record("task", job.get("provider", "quark"), job.get("keyword", ""),
                                        job["state"], task_id=key, detail=job.get("error", ""))
                elif job is None and previous is not None:
                    self.history.record("task", previous.get("provider", "quark"),
                                        previous.get("keyword", ""), "removed", task_id=key)
        self._last_history = {key: dict(job) for key, job in self.jobs.items()}

    def add(self, job_id, share_id, keyword, episodes, *, save_only=False, identity_import=False, aliyun_result=None):
        with self.lock:
            if job_id in self.jobs:
                return False
            self.jobs[job_id] = {
                "share": share_id, "keyword": keyword, "episodes": episodes,
                "state": "pending", "files": [], "save_only": save_only,
                "identity_import": identity_import,
            }
            if aliyun_result is not None:
                self.jobs[job_id].update(
                    provider="aliyun", state="pending" if self.aliyun_auto else "saved",
                    title=aliyun_result.candidate.title,
                    files=[asdict(file) for file in aliyun_result.files],
                )
            elif save_only:
                name = re.sub(r'[\x00-\x1f/\\:*?"<>|]', " ", keyword).strip(" .")[:36]
                if name:
                    self.jobs[job_id]["desktop_folder"] = f"{name} - {job_id}"
            try:
                self._save()
            except OSError:
                self.jobs.pop(job_id)
                raise
        self.wake.set()
        return True

    def snapshot(self):
        with self.lock:
            return {key: dict(job) for key, job in self.jobs.items()}

    def transition(self, keys, allowed, state):
        with self.lock:
            if self.stop.is_set():
                return False
            changed = False
            for key in keys:
                job = self.jobs.get(key)
                if job and job["state"] in allowed:
                    job.update(state=state, error="")
                    changed = True
            if changed:
                self._save()
        if changed:
            self.wake.set()
        return changed

    def close(self):
        with self.lock:
            self.stop.set()
        self.wake.set()
        self.thread.join(timeout=1)

    def remove(self, keys, delete_files):
        with self.lock:
            found = [key for key in keys if key in self.jobs]
            if not found:
                return None
            if any(self.jobs[key]["state"] in {"running", "pending"} for key in found):
                return False
            for key in found:
                try:
                    self.worker.remove(key, delete_files)
                except (JobError, QuarkError, OSError):
                    return False
                del self.jobs[key]
                self.speed_samples.pop(key, None)
                self._save()
        return True

    def _loop(self):
        while not self.stop.is_set():
            self.wake.clear()
            with self.lock:
                next_job = next((key for key, job in self.jobs.items()
                                 if job["state"] == "pending"), None)
            if next_job is None:
                self.wake.wait()
                continue
            if self.transition([next_job], {"pending"}, "running"):
                self._run(next_job)

    def _run(self, key):
        def check():
            with self.lock:
                if self.stop.is_set() or self.jobs[key]["state"] != "running":
                    raise JobWaiting("Job interrupted")

        async def source(job):
            try:
                candidates = await self.discovery.candidates(job["keyword"])
            except DiscoveryError:
                candidates = []
            return next((item for item in candidates if item.share_id == job["share"]),
                        Candidate(job["share"], job["keyword"], ""))

        try:
            job = self.snapshot()[key]
            if job.get("provider") == "aliyun":
                self._run_aliyun(key, job, check)
                return
            refresh = None
            if job["files"]:
                candidate = Candidate(job["share"], job.get("title") or job["keyword"], "")
                files = tuple(ShareFile(**file) for file in job["files"])
                refresh = lambda: asyncio.run(source(job))
            else:
                candidate = asyncio.run(source(job))
                episodes = set(job["episodes"]) if job["episodes"] else None
                files = asyncio.run(self.discovery.resolve(candidate, episodes)).files
            with self.lock:
                check()
                self.jobs[key].update(title=candidate.title, files=[asdict(file) for file in files])
                self._save()
            save_only = job.get("save_only", False)
            self.worker.run(key, candidate, files, check=check, refresh=refresh, save_only=save_only,
                            desktop_folder=job.get("desktop_folder"))
            self.transition([key], {"running", "paused"}, "saved" if save_only else "completed")
        except Exception as error:
            with self.lock:
                if self.jobs[key]["state"] == "running" and not self.stop.is_set():
                    self.jobs[key].update(state="waiting" if isinstance(error, JobWaiting) else "failed",
                                          error=type(error).__name__)
                    self._save()

    def _run_aliyun(self, key, job, check):
        if not self.aliyun_auto:
            raise JobWaiting("Alipan automatic saving is disabled")
        candidates = asyncio.run(self.aliyun_discovery.candidates(job["keyword"]))
        candidate = next((item for item in candidates if item.share_id == job["share"]),
                         Candidate(job["share"], job.get("title") or job["keyword"], ""))
        request = {"id": key, "share": candidate.share_id, "title": candidate.title,
                   "passcode": candidate.passcode, "files": job["files"],
                   "state_dir": str(self.worker.state_dir),
                   "download_dir": str(self.worker.download_dir)}
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(Path(__file__).resolve().parent.parent), environment.get("PYTHONPATH", "")])
        check()
        process = subprocess.run([sys.executable, "-m", "cloudshare.aliyun_save"],
                                 input=json.dumps(request), capture_output=True, text=True,
                                 env=environment, timeout=180)
        if process.returncode:
            raise JobWaiting("Alipan background save needs attention")
        result = json.loads(process.stdout)
        if (set(result) != {"userId", "driveId", "folderID"}
                or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)
                       for value in result.values())):
            raise JobError("Invalid Alipan dispatch identity")
        with self.lock:
            check()
            self.jobs[key].update(desktop=result, state="saved", error="",
                                  dispatch_attempt=job.get("dispatch_attempt", 0) + 1)
            self._save()

    def metrics(self, key, job):
        total = sum(file["size"] for file in job["files"])
        if job["state"] in {"completed", "transferred", "saved"}:
            return total, total, 100.0, 0.0
        completed = 0
        for index, file in enumerate(job["files"]):
            paths = (self.worker.download_dir / key / file["path"],
                     self.worker.download_dir / ".cloudshare-parts" / key / str(index))
            for path in paths:
                try:
                    completed += min(path.stat().st_size, file["size"])
                    break
                except FileNotFoundError:
                    continue
        speed = 0.0
        with self.lock:
            if job["state"] == "running" and key in self.jobs:
                now = time.monotonic()
                previous = self.speed_samples.get(key)
                if previous is None:
                    self.speed_samples[key] = now, completed, speed
                else:
                    timestamp, size, speed = previous
                    if now - timestamp >= 1:
                        speed = max(0, completed - size) / (now - timestamp)
                        self.speed_samples[key] = now, completed, speed
            else:
                self.speed_samples.pop(key, None)
        progress = min(99.9, 100 * completed / total) if total else 0.0
        return total, completed, progress, speed
