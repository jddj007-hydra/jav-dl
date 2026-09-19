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
    cleaned INTEGER NOT NULL DEFAULT 0
);
"""


class Database:
    def __init__(self, settings: Settings):
        self.path = str(settings.db_path)

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

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
