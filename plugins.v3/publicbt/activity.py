# SPDX-License-Identifier: GPL-3.0-only
"""Private credentials and bounded activity storage for standalone PublicBT."""

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
from threading import RLock


def clean_text(value):
    text = re.sub(r"(?:https?://|magnet:)\S+", "", str(value or ""), flags=re.I)
    text = re.sub(r"(?:api[_ -]?(?:key|hash)|cookie|session|authorization|token|password)\s*[:=]\s*\S+",
                  "", text, flags=re.I)
    return " ".join(text.split())[:256]


def read_key(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return ""
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 16384:
            raise ValueError("Invalid private key file")
        return stream.read(16385).strip()


def write_key(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".prowlarr-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class UsageHistory:
    def __init__(self, path, *, enabled=True, limit=1000):
        self.path = Path(path)
        self.enabled = enabled
        self.limit = max(1, min(5000, int(limit)))
        self.lock = RLock()
        self.failed = False
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("History must be a regular file")
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, time TEXT, "
                       "kind TEXT, title TEXT, status TEXT, count INTEGER, detail TEXT)")

    @contextmanager
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    def record(self, kind, title, status, *, count=0, detail=""):
        if not self.enabled:
            return
        try:
            with self.connection() as db:
                db.execute("INSERT INTO events (time,kind,title,status,count,detail) VALUES (?,?,?,?,?,?)", (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"), clean_text(kind),
                    clean_text(title), clean_text(status), max(0, int(count)), clean_text(detail)))
                db.execute("DELETE FROM events WHERE id NOT IN "
                           "(SELECT id FROM events ORDER BY id DESC LIMIT ?)", (self.limit,))
        except (sqlite3.Error, OSError):
            self.failed = True

    def recent(self):
        try:
            with self.connection() as db:
                return [dict(row) for row in db.execute(
                    "SELECT * FROM events ORDER BY id DESC LIMIT ?", (self.limit,))]
        except (sqlite3.Error, OSError):
            self.failed = True
            return []
