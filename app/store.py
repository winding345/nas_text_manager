"""SQLite 存储：书籍索引、阅读进度、设置。"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    ext TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    root TEXT NOT NULL DEFAULT '',
    folder TEXT NOT NULL DEFAULT '',
    char_count INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_books_name ON books(name);
CREATE TABLE IF NOT EXISTS progress (
    book_id INTEGER PRIMARY KEY,
    char_offset INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._cursor() as cur:
            cur.executescript(SCHEMA)
            # 老库自动补列（无迁移框架，做最小兼容）
            try:
                cur.execute("SELECT folder FROM books LIMIT 1")
            except sqlite3.OperationalError:
                cur.execute("ALTER TABLE books ADD COLUMN folder TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def _cursor(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn.cursor()
            conn.commit()
        finally:
            conn.close()

    # ---------- 书籍 ----------
    def upsert_book(self, path: str, name: str, ext: str, size: int,
                    mtime: float, root: str, folder: str = "") -> bool:
        """返回是否新增（用于统计）。"""
        with self._cursor() as cur:
            cur.execute("SELECT id, size, mtime FROM books WHERE path=?", (path,))
            row = cur.fetchone()
            if row:
                if int(row["size"]) == size and abs(float(row["mtime"]) - mtime) < 1e-6:
                    return False
                cur.execute(
                    "UPDATE books SET name=?, size=?, mtime=?, root=?, folder=?, char_count=0 WHERE id=?",
                    (name, size, mtime, root, folder, row["id"]))
                return False
            cur.execute(
                "INSERT INTO books(path,name,ext,size,mtime,root,folder,added_at) VALUES (?,?,?,?,?,?,?,?)",
                (path, name, ext, size, mtime, root, folder, time.time()))
            return True

    def get_book(self, book_id: int) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM books WHERE id=?", (book_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def list_books(self, terms: Iterable[str] = (), limit: int = 200,
                   folder: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT id,name,ext,size,root,folder,char_count,path FROM books"
        params: List[Any] = []
        terms = [t for t in terms if t]
        where = []
        if terms:
            where.append(" AND ".join("name LIKE ?" for _ in terms))
            params += ["%%%s%%" % t for t in terms]
        if folder:
            where.append("folder = ?")
            params.append(folder)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY name LIMIT ?"
        params.append(limit)
        with self._cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def delete_book(self, book_id: int) -> Optional[Dict[str, Any]]:
        book = self.get_book(book_id)
        if not book:
            return None
        with self._cursor() as cur:
            cur.execute("DELETE FROM books WHERE id=?", (book_id,))
            cur.execute("DELETE FROM progress WHERE book_id=?", (book_id,))
        return book

    def set_char_count(self, book_id: int, count: int) -> None:
        with self._cursor() as cur:
            cur.execute("UPDATE books SET char_count=? WHERE id=?", (count, book_id))

    def groups(self) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            cur.execute("SELECT folder, COUNT(*) AS n FROM books GROUP BY folder ORDER BY folder")
            return [{"name": r["folder"], "count": int(r["n"])} for r in cur.fetchall()]

    def count_books(self) -> int:
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM books")
            return int(cur.fetchone()[0])

    # ---------- 进度 ----------
    def get_progress(self, book_id: int) -> int:
        with self._cursor() as cur:
            cur.execute("SELECT char_offset FROM progress WHERE book_id=?", (book_id,))
            row = cur.fetchone()
        return int(row["char_offset"]) if row else 0

    def set_progress(self, book_id: int, offset: int) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO progress(book_id,char_offset,updated_at) VALUES (?,?,?) "
                "ON CONFLICT(book_id) DO UPDATE SET char_offset=excluded.char_offset, "
                "updated_at=excluded.updated_at",
                (book_id, max(0, int(offset)), time.time()))

    # ---------- 设置 ----------
    def get_settings(self) -> Dict[str, str]:
        with self._cursor() as cur:
            cur.execute("SELECT k,v FROM settings")
            return {r["k"]: r["v"] for r in cur.fetchall()}

    def put_settings(self, data: Dict[str, str]) -> None:
        with self._cursor() as cur:
            for k, v in data.items():
                cur.execute(
                    "INSERT INTO settings(k,v) VALUES (?,?) "
                    "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(k), str(v)))
