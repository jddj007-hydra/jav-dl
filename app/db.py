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
    backend TEXT NOT NULL DEFAULT '',
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
CREATE TABLE IF NOT EXISTS western_library (
    path TEXT PRIMARY KEY,
    tpdb_id TEXT NOT NULL DEFAULT '',
    studio TEXT NOT NULL,
    title TEXT NOT NULL,
    has_nfo INTEGER NOT NULL DEFAULT 0,
    has_poster INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    target TEXT NOT NULL,
    auto INTEGER NOT NULL DEFAULT 0,
    want_uc INTEGER NOT NULL DEFAULT 0,
    want_c INTEGER NOT NULL DEFAULT 0,
    max_gb INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    last_check REAL,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS subscription_seen (
    sub_id TEXT NOT NULL,
    code TEXT NOT NULL,
    PRIMARY KEY (sub_id, code)
);
CREATE TABLE IF NOT EXISTS subscription_hits (
    id TEXT PRIMARY KEY,
    sub_id TEXT NOT NULL,
    code TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    created_at REAL NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0
);
"""

DOWNLOAD_COLUMNS = (
    ("scrape_status", "TEXT NOT NULL DEFAULT ''"),
    ("scrape_error", "TEXT"),
    ("archive_path", "TEXT"),
    ("backend", "TEXT NOT NULL DEFAULT ''"),
)

LIBRARY_COLUMNS = (
    ("title", "TEXT NOT NULL DEFAULT ''"),
    ("actors", "TEXT NOT NULL DEFAULT '[]'"),
    ("release_date", "TEXT NOT NULL DEFAULT ''"),
    ("added_at", "REAL NOT NULL DEFAULT 0"),
)

WESTERN_COLUMNS = (
    ("actors", "TEXT NOT NULL DEFAULT '[]'"),
    ("release_date", "TEXT NOT NULL DEFAULT ''"),
    ("added_at", "REAL NOT NULL DEFAULT 0"),
)


def _actors_json(value) -> str:
    if isinstance(value, str):
        return value or "[]"
    names = []
    for item in value or []:
        name = item.get("name") if isinstance(item, dict) else item
        name = str(name or "").strip()
        if name and name not in names:
            names.append(name)
    return json.dumps(names, ensure_ascii=False)


def _library_row(row: dict, now: float) -> dict:
    return {
        "code": row["code"],
        "month": row["month"],
        "path": row["path"],
        "has_video": 1 if row.get("has_video", True) else 0,
        "has_nfo": 1 if row.get("has_nfo") else 0,
        "has_poster": 1 if row.get("has_poster") else 0,
        "title": row.get("title") or "",
        "actors": _actors_json(row.get("actors")),
        "release_date": row.get("release_date") or "",
        "added_at": float(row.get("added_at") or now),
        "updated_at": now,
    }


def _western_row(row: dict, now: float) -> dict:
    return {
        "path": row["path"],
        "tpdb_id": row.get("tpdb_id") or "",
        "studio": row.get("studio") or "",
        "title": row.get("title") or "",
        "has_nfo": 1 if row.get("has_nfo") else 0,
        "has_poster": 1 if row.get("has_poster") else 0,
        "actors": _actors_json(row.get("actors")),
        "release_date": row.get("release_date") or "",
        "added_at": float(row.get("added_at") or now),
        "updated_at": now,
    }


class Database:
    def __init__(self, settings: Settings):
        self.path = str(settings.db_path)

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            for table, columns in (
                ("downloads", DOWNLOAD_COLUMNS),
                ("library", LIBRARY_COLUMNS),
                ("western_library", WESTERN_COLUMNS),
            ):
                cur = await db.execute(f"PRAGMA table_info({table})")
                cols = {row[1] for row in await cur.fetchall()}
                for name, ddl in columns:
                    if name not in cols:
                        await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
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
        row = dict(job)
        row.setdefault("backend", "")
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO downloads
                   (id, code, info_hash, title, magnet, gid, status, dest, error, created_at, updated_at, cleaned, backend)
                   VALUES (:id,:code,:info_hash,:title,:magnet,:gid,:status,:dest,:error,:created_at,:updated_at,:cleaned,:backend)""",
                row,
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

    async def delete_job(self, job_id: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("DELETE FROM downloads WHERE id = ?", (job_id,))
            await db.commit()
            return cur.rowcount > 0

    async def delete_jobs_with_status(self, statuses: list[str]) -> int:
        if not statuses:
            return 0
        marks = ",".join("?" for _ in statuses)
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                f"DELETE FROM downloads WHERE status IN ({marks})",
                statuses,
            )
            await db.commit()
            return cur.rowcount or 0

    async def replace_library(self, rows: list[dict]) -> None:
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM library")
            await db.executemany(
                """INSERT INTO library
                   (code, month, path, has_video, has_nfo, has_poster,
                    title, actors, release_date, added_at, updated_at)
                   VALUES (:code, :month, :path, :has_video, :has_nfo, :has_poster,
                    :title, :actors, :release_date, :added_at, :updated_at)""",
                [_library_row(row, now) for row in rows],
            )
            await db.commit()

    async def upsert_library(self, row: dict) -> None:
        payload = _library_row(row, time.time())
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO library
                   (code, month, path, has_video, has_nfo, has_poster,
                    title, actors, release_date, added_at, updated_at)
                   VALUES (:code, :month, :path, :has_video, :has_nfo, :has_poster,
                    :title, :actors, :release_date, :added_at, :updated_at)
                   ON CONFLICT(code) DO UPDATE SET
                     month=excluded.month,
                     path=excluded.path,
                     has_video=excluded.has_video,
                     has_nfo=excluded.has_nfo,
                     has_poster=excluded.has_poster,
                     title=excluded.title,
                     actors=excluded.actors,
                     release_date=excluded.release_date,
                     added_at=excluded.added_at,
                     updated_at=excluded.updated_at""",
                payload,
            )
            await db.commit()

    async def list_library(self) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM library ORDER BY month DESC, code")
            rows = await cur.fetchall()
        return [dict(row) for row in rows]

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

    async def replace_western(self, rows: list[dict]) -> None:
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM western_library")
            if rows:
                await db.executemany(
                    """INSERT INTO western_library
                       (path, tpdb_id, studio, title, has_nfo, has_poster,
                        actors, release_date, added_at, updated_at)
                       VALUES (:path, :tpdb_id, :studio, :title, :has_nfo, :has_poster,
                        :actors, :release_date, :added_at, :updated_at)""",
                    [_western_row(row, now) for row in rows],
                )
            await db.commit()

    async def upsert_western(self, row: dict) -> None:
        payload = _western_row(row, time.time())
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO western_library
                   (path, tpdb_id, studio, title, has_nfo, has_poster,
                    actors, release_date, added_at, updated_at)
                   VALUES (:path, :tpdb_id, :studio, :title, :has_nfo, :has_poster,
                    :actors, :release_date, :added_at, :updated_at)
                   ON CONFLICT(path) DO UPDATE SET
                     tpdb_id=excluded.tpdb_id,
                     studio=excluded.studio,
                     title=excluded.title,
                     has_nfo=excluded.has_nfo,
                     has_poster=excluded.has_poster,
                     actors=excluded.actors,
                     release_date=excluded.release_date,
                     added_at=excluded.added_at,
                     updated_at=excluded.updated_at""",
                payload,
            )
            await db.commit()

    async def list_western(self) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM western_library ORDER BY studio, title"
            )
            rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def western_by_ids(self, ids: list[str]) -> dict[str, dict]:
        uniq = [item for item in dict.fromkeys(ids) if item]
        if not uniq:
            return {}
        placeholders = ",".join("?" * len(uniq))
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT * FROM western_library WHERE tpdb_id IN ({placeholders})",
                uniq,
            )
            rows = await cur.fetchall()
        found: dict[str, dict] = {}
        for row in rows:
            found.setdefault(row["tpdb_id"], dict(row))
        return found

    async def add_subscription(self, row: dict) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO subscriptions
                   (id, kind, name, target, auto, want_uc, want_c, max_gb, created_at, last_check, last_error)
                   VALUES (:id, :kind, :name, :target, :auto, :want_uc, :want_c, :max_gb, :created_at, :last_check, :last_error)""",
                row,
            )
            await db.commit()

    async def list_subscriptions(self) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM subscriptions ORDER BY created_at DESC")
            rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def get_subscription(self, sub_id: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,))
            row = await cur.fetchone()
        return dict(row) if row else None

    async def delete_subscription(self, sub_id: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM subscription_hits WHERE sub_id = ?", (sub_id,))
            await db.execute("DELETE FROM subscription_seen WHERE sub_id = ?", (sub_id,))
            await db.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
            await db.commit()

    async def mark_subscription_checked(self, sub_id: str, *, error: str | None, stamp: bool = True) -> None:
        """stamp=False keeps last_check, so a failed or empty first check stays a first check."""
        async with aiosqlite.connect(self.path) as db:
            if stamp:
                await db.execute(
                    "UPDATE subscriptions SET last_check = ?, last_error = ? WHERE id = ?",
                    (time.time(), error, sub_id),
                )
            else:
                await db.execute(
                    "UPDATE subscriptions SET last_error = ? WHERE id = ?",
                    (error, sub_id),
                )
            await db.commit()

    async def seen_codes(self, sub_id: str) -> set[str]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT code FROM subscription_seen WHERE sub_id = ?", (sub_id,))
            rows = await cur.fetchall()
        return {row[0] for row in rows}

    async def mark_seen(self, sub_id: str, code: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO subscription_seen (sub_id, code) VALUES (?, ?)",
                (sub_id, code),
            )
            await db.commit()

    async def add_hit(self, row: dict) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO subscription_hits
                   (id, sub_id, code, title, status, detail, created_at, seen)
                   VALUES (:id, :sub_id, :code, :title, :status, :detail, :created_at, 0)""",
                row,
            )
            await db.commit()

    async def find_hit(self, sub_id: str, code: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT * FROM subscription_hits
                   WHERE sub_id = ? AND code = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (sub_id, code),
            )
            row = await cur.fetchone()
        return dict(row) if row else None

    async def update_hit(self, hit_id: str, *, status: str, detail: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE subscription_hits SET status = ?, detail = ?, seen = 0, created_at = ?
                   WHERE id = ?""",
                (status, detail, time.time(), hit_id),
            )
            await db.commit()

    async def pending_hits(self, sub_id: str) -> list[dict]:
        """no_magnet hits not yet remembered as seen, so a later check can retry them."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT h.code, MIN(h.created_at) AS created_at FROM subscription_hits h
                   WHERE h.sub_id = ? AND h.status = 'no_magnet'
                     AND NOT EXISTS (
                       SELECT 1 FROM subscription_seen s WHERE s.sub_id = h.sub_id AND s.code = h.code
                     )
                   GROUP BY h.code""",
                (sub_id,),
            )
            rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def list_hits(self, limit: int = 50) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM subscription_hits ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def read_hit(self, hit_id: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE subscription_hits SET seen = 1 WHERE id = ?", (hit_id,))
            await db.commit()

    async def count_unread_hits(self) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM subscription_hits WHERE seen = 0")
            row = await cur.fetchone()
        return int(row[0] if row else 0)

    async def code_in_queue(self, code: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT 1 FROM downloads WHERE code = ? AND status != 'cancelled' LIMIT 1",
                (code,),
            )
            return await cur.fetchone() is not None

    async def western_has_id(self, tpdb_id: str) -> bool:
        if not tpdb_id:
            return False
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT 1 FROM western_library WHERE tpdb_id = ? LIMIT 1",
                (tpdb_id,),
            )
            return await cur.fetchone() is not None
