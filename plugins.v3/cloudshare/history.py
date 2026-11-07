# SPDX-License-Identifier: GPL-3.0-only
"""Bounded, private usage history independent of the active download queue."""

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
import stat
from threading import RLock


def clean_text(value):
    text = re.sub(r"https?://\S+", "", str(value or ""), flags=re.I)
    text = re.sub(r"(?:提取码|提取碼|密码|密碼)\s*[:：]?\s*\S+", "", text)
    text = re.sub(r"(?:api[_ -]?hash|cookie|session|authorization|token|password)\s*[:=]\s*\S+",
                  "", text, flags=re.I)
    return " ".join(text.split())[:256]


class UsageHistory:
    def __init__(self, path, *, enabled=True, limit=1000):
        self.path = Path(path)
        self.enabled = enabled
        self.limit = max(1, min(5000, int(limit)))
        self.lock = RLock()
        self.failed = False
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("History must be a regular file")
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        with self._connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, "
                       "time TEXT, kind TEXT, provider TEXT, title TEXT, status TEXT, "
                       "task_id TEXT, count INTEGER, detail TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")

    @contextmanager
    def _connection(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    def _insert(self, db, kind, provider, title, status, task_id="", count=0, detail=""):
        db.execute("INSERT INTO events (time,kind,provider,title,status,task_id,count,detail) "
                   "VALUES (?,?,?,?,?,?,?,?)", (
                       datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       clean_text(kind), clean_text(provider), clean_text(title), clean_text(status),
                       clean_text(task_id), max(0, int(count)), clean_text(detail)))

    def _prune(self, db):
        db.execute("DELETE FROM events WHERE id NOT IN "
                   "(SELECT id FROM events ORDER BY id DESC LIMIT ?)", (self.limit,))

    def record(self, kind, provider, title, status, **kwargs):
        if not self.enabled:
            return
        try:
            with self._connection() as db:
                self._insert(db, kind, provider, title, status, **kwargs)
                self._prune(db)
        except (sqlite3.Error, OSError):
            self.failed = True

    def seed_tasks(self, jobs):
        if not self.enabled:
            return
        try:
            with self._connection() as db:
                if db.execute("SELECT 1 FROM metadata WHERE key='existing_tasks'").fetchone():
                    return
                for key, job in jobs.items():
                    self._insert(db, "snapshot", job.get("provider", "quark"),
                                 job.get("keyword", ""), job["state"], task_id=key,
                                 detail="existing_task")
                self._prune(db)
                db.execute("INSERT INTO metadata VALUES ('existing_tasks','1')")
        except (sqlite3.Error, OSError):
            self.failed = True

    def recent(self):
        try:
            with self._connection() as db:
                return [dict(row) for row in db.execute(
                    "SELECT * FROM events ORDER BY id DESC LIMIT ?", (self.limit,))]
        except (sqlite3.Error, OSError):
            self.failed = True
            return []
