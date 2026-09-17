from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True, slots=True)
class Artifact:
    id: str
    filename: str
    slug: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: int
    expires_at: int


SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    slug TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    sha256 TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS artifacts_expires_at ON artifacts(expires_at);
CREATE TABLE IF NOT EXISTS plans (
    name TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    updated_at INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, artifact_id: str) -> Artifact | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
        return Artifact(**dict(row)) if row else None

    def total_size(self, connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM artifacts"
        ).fetchone()
        return int(row["total"])

    def insert(self, connection: sqlite3.Connection, artifact: Artifact) -> None:
        connection.execute(
            """
            INSERT INTO artifacts
                (id, filename, slug, media_type, size_bytes, sha256, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.id,
                artifact.filename,
                artifact.slug,
                artifact.media_type,
                artifact.size_bytes,
                artifact.sha256,
                artifact.created_at,
                artifact.expires_at,
            ),
        )

    def get_plan(self, name: str) -> Artifact | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT artifacts.*
                FROM plans
                JOIN artifacts ON artifacts.id = plans.artifact_id
                WHERE plans.name = ?
                """,
                (name,),
            ).fetchone()
        return Artifact(**dict(row)) if row else None

    def set_plan(
        self,
        connection: sqlite3.Connection,
        name: str,
        artifact_id: str,
        updated_at: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO plans (name, artifact_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                artifact_id = excluded.artifact_id,
                updated_at = excluded.updated_at
            """,
            (name, artifact_id, updated_at),
        )

    def delete(self, connection: sqlite3.Connection, artifact_id: str) -> None:
        connection.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))

    def expired(self, connection: sqlite3.Connection, now: int) -> list[Artifact]:
        rows = connection.execute(
            "SELECT * FROM artifacts WHERE expires_at <= ? ORDER BY expires_at", (now,)
        ).fetchall()
        return [Artifact(**dict(row)) for row in rows]

    def all_ids(self) -> set[str]:
        with self.connect() as connection:
            return {row["id"] for row in connection.execute("SELECT id FROM artifacts")}
