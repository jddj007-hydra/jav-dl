from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite

from app.config import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata_cache (
    code TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS downloads (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    info_hash TEXT NOT NULL,
    title TEXT NOT NULL,
    magnet TEXT NOT NULL,
    gid TEXT,
    status TEXT NOT NULL,
    dest TEXT NOT NULL,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    cleaned INTEGER NOT NULL DEFAULT 0,
    scrape_status TEXT NOT NULL DEFAULT '',
    scrape_error TEXT,
    archive_path TEXT
);
CREATE TABLE IF NOT EXISTS library (
    code TEXT PRIMARY KEY,
    month TEXT NOT NULL,
    path TEXT NOT NULL,
    has_video INTEGER NOT NULL DEFAULT 0,
    has_nfo INTEGER NOT NULL DEFAULT 0,
    has_poster INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
"""

DOWNLOAD_COLUMNS = (
    ("scrape_status", "TEXT NOT NULL DEFAULT ''"),
    ("scrape_error", "TEXT"),
    ("archive_path", "TEXT"),
)


class Database:
    def __init__(self, settings: Settings):
        self.path = str(settings.db_path)

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            cur = await db.execute("PRAGMA table_info(downloads)")
            cols = {row[1] for row in await cur.fetchall()}
            for name, ddl in DOWNLOAD_COLUMNS:
                if name not in cols:
                    await db.execute(f"ALTER TABLE downloads ADD COLUMN {name} {ddl}")
            await db.commit()

    async def get_metadata_any(self, code: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT payload FROM metadata_cache WHERE code = ?",
                (code,),
            )
            row = await cur.fetchone()
        if not row:
            return None
        return json.loads(row["payload"])

    async def get_metadata(self, code: str, ttl: int) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT payload, fetched_at FROM metadata_cache WHERE code = ?",
                (code,),
            )
            row = await cur.fetchone()
        if not row:
            return None
        if time.time() - row["fetched_at"] > ttl:
            return None
        return json.loads(row["payload"])

    async def put_metadata(self, code: str, payload: dict) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO metadata_cache (code, payload, fetched_at) VALUES (?,?,?)",
                (code, json.dumps(payload, ensure_ascii=False), time.time()),
            )
            await db.commit()

    async def insert_job(self, job: dict) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO downloads
                   (id, code, info_hash, title, magnet, gid, status, dest, error, created_at, updated_at, cleaned)
                   VALUES (:id,:code,:info_hash,:title,:magnet,:gid,:status,:dest,:error,:created_at,:updated_at,:cleaned)""",
                job,
            )
            await db.commit()

    async def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields = dict(fields)
        fields["updated_at"] = time.time()
        assignments = ", ".join(f"{k} = :{k}" for k in fields)
        fields["id"] = job_id
        async with aiosqlite.connect(self.path) as db:
            await db.execute(f"UPDATE downloads SET {assignments} WHERE id = :id", fields)
            await db.commit()

    async def get_job(self, job_id: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM downloads WHERE id = ?", (job_id,))
            row = await cur.fetchone()
        return dict(row) if row else None

    async def find_job_by_hash(self, info_hash: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM downloads WHERE info_hash = ? ORDER BY created_at DESC LIMIT 1",
                (info_hash.lower(),),
            )
            row = await cur.fetchone()
        return dict(row) if row else None

    async def list_jobs(self) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM downloads ORDER BY created_at DESC")
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def replace_library(self, rows: list[dict]) -> None:
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM library")
            await db.executemany(
                """INSERT INTO library
                   (code, month, path, has_video, has_nfo, has_poster, updated_at)
                   VALUES (:code, :month, :path, :has_video, :has_nfo, :has_poster, :updated_at)""",
                [{**row, "updated_at": now} for row in rows],
            )
            await db.commit()

    async def upsert_library(self, row: dict) -> None:
        payload = {
            "code": row["code"],
            "month": row["month"],
            "path": row["path"],
            "has_video": 1 if row.get("has_video", True) else 0,
            "has_nfo": 1 if row.get("has_nfo") else 0,
            "has_poster": 1 if row.get("has_poster") else 0,
            "updated_at": time.time(),
        }
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO library
                   (code, month, path, has_video, has_nfo, has_poster, updated_at)
                   VALUES (:code, :month, :path, :has_video, :has_nfo, :has_poster, :updated_at)
                   ON CONFLICT(code) DO UPDATE SET
                     month=excluded.month,
                     path=excluded.path,
                     has_video=excluded.has_video,
                     has_nfo=excluded.has_nfo,
                     has_poster=excluded.has_poster,
                     updated_at=excluded.updated_at""",
                payload,
            )
            await db.commit()

    async def get_library(self, code: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM library WHERE code = ?", (code,))
            row = await cur.fetchone()
        return dict(row) if row else None

    async def get_library_many(self, codes: list[str]) -> dict[str, dict]:
        if not codes:
            return {}
        uniq = list(dict.fromkeys(codes))
        placeholders = ",".join("?" * len(uniq))
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT * FROM library WHERE code IN ({placeholders})",
                uniq,
            )
            rows = await cur.fetchall()
        return {row["code"]: dict(row) for row in rows}
