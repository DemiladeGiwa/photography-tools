"""
On-disk analysis cache (SQLite, inside ``<library>/.photo_sorter_cache``).

Rows are keyed by a fingerprint of *name + size + modification time*, not
the full path, so photos keep their cached analysis after being moved or
organised into subfolders (and copies made with ``copy2`` hit too). Editing
or replacing a file changes size/mtime and naturally invalidates its row.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .constants import CACHE_DB_NAME, CACHE_DIRNAME

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signatures (
    fingerprint TEXT PRIMARY KEY,
    path        TEXT NOT NULL,
    data_json   TEXT NOT NULL,
    thumb_jpeg  BLOB,
    updated_at  REAL NOT NULL
);
"""


def fingerprint_for(path: Path) -> Optional[str]:
    try:
        st = path.stat()
    except OSError:
        return None
    return f"{path.name.lower()}|{st.st_size}|{st.st_mtime_ns}"


class SignatureCache:
    """Thread-safe sqlite-backed cache of analysis results + thumbnails."""

    def __init__(self, folder: Path):
        self.dir = Path(folder) / CACHE_DIRNAME
        self.dir.mkdir(parents=True, exist_ok=True)
        _hide_on_windows(self.dir)
        self.db_path = self.dir / CACHE_DB_NAME
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        self._pending = 0

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                pass

    def get(self, path: Path) -> Optional[Dict[str, Any]]:
        fp = fingerprint_for(path)
        if fp is None:
            return None
        with self._lock:
            row = self._conn.execute("SELECT data_json FROM signatures WHERE fingerprint = ?", (fp,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    def get_many(self, paths: Iterable[Path]) -> Dict[str, Dict[str, Any]]:
        """Cached analysis for many files in a few queries: {str(path): data}. Misses are left out."""
        by_fp: Dict[str, str] = {}
        for p in paths:
            fp = fingerprint_for(p)
            if fp is not None:
                by_fp[fp] = str(p)
        out: Dict[str, Dict[str, Any]] = {}
        fps = list(by_fp)
        for i in range(0, len(fps), 500):
            chunk = fps[i:i + 500]
            q = "SELECT fingerprint, data_json FROM signatures WHERE fingerprint IN (%s)" % ",".join("?" * len(chunk))
            with self._lock:
                rows = self._conn.execute(q, chunk).fetchall()
            for fp, js in rows:
                try:
                    out[by_fp[fp]] = json.loads(js)
                except json.JSONDecodeError:
                    pass
        return out

    def get_thumbnail(self, path: Path) -> Optional[bytes]:
        fp = fingerprint_for(path)
        if fp is None:
            return None
        with self._lock:
            row = self._conn.execute("SELECT thumb_jpeg FROM signatures WHERE fingerprint = ?", (fp,)).fetchone()
        return row[0] if row and row[0] is not None else None

    def put(self, path: Path, data: Dict[str, Any], thumb_jpeg: Optional[bytes] = None) -> None:
        fp = fingerprint_for(path)
        if fp is None:
            return
        with self._lock:
            self._conn.execute(
                """INSERT INTO signatures (fingerprint, path, data_json, thumb_jpeg, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(fingerprint) DO UPDATE SET
                       path = excluded.path,
                       data_json = excluded.data_json,
                       thumb_jpeg = COALESCE(excluded.thumb_jpeg, signatures.thumb_jpeg),
                       updated_at = excluded.updated_at""",
                (fp, str(path), json.dumps(data), thumb_jpeg, time.time()),
            )
            self._pending += 1
            if self._pending >= 25:
                self._conn.commit()
                self._pending = 0

    def flush(self) -> None:
        with self._lock:
            self._conn.commit()
            self._pending = 0

    def rename(self, old_name: str, new_path: Path) -> None:
        """Keep a cached row when a file was renamed (size and mtime are unchanged by a rename)."""
        try:
            st = new_path.stat()
        except OSError:
            return
        old_fp = f"{old_name.lower()}|{st.st_size}|{st.st_mtime_ns}"
        new_fp = f"{new_path.name.lower()}|{st.st_size}|{st.st_mtime_ns}"
        if old_fp == new_fp:
            return
        with self._lock:
            try:
                self._conn.execute("UPDATE OR IGNORE signatures SET fingerprint = ?, path = ? WHERE fingerprint = ?",
                                   (new_fp, str(new_path), old_fp))
                self._conn.commit()
            except sqlite3.Error:
                pass

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM signatures").fetchone()[0]
            thumbs = self._conn.execute("SELECT COUNT(*) FROM signatures WHERE thumb_jpeg IS NOT NULL").fetchone()[0]
        size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {"total_entries": total, "thumb_entries": thumbs, "size_mb": round(size / 1048576, 2)}

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM signatures")
            self._conn.commit()
            self._conn.execute("VACUUM")


def _hide_on_windows(path: Path) -> None:
    import sys

    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)  # FILE_ATTRIBUTE_HIDDEN
    except Exception:
        pass
