from __future__ import annotations

import asyncio
import fcntl
import hashlib
import os
import re
import secrets
import shutil
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import AsyncIterator, Iterator

from .config import Config
from .database import Artifact, Database

ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ArtifactTooLarge(Exception):
    pass


class StorageQuotaExceeded(Exception):
    pass


class ArtifactMissing(Exception):
    pass


class Storage:
    def __init__(self, config: Config, database: Database):
        self.config = config
        self.database = database
        self.upload_slots = asyncio.Semaphore(config.max_concurrent_uploads)

    @staticmethod
    def valid_id(artifact_id: str) -> bool:
        return bool(ARTIFACT_ID_RE.fullmatch(artifact_id))

    def object_directory(self, artifact_id: str) -> Path:
        return self.config.objects_dir / artifact_id[:2] / artifact_id

    def object_path(self, artifact_id: str) -> Path:
        return self.object_directory(artifact_id) / "payload"

    @contextmanager
    def maintenance_lock(self) -> Iterator[None]:
        """Serialize destructive storage maintenance across service processes."""
        lock_path = self.config.data_dir / ".maintenance.lock"
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("Artifact Hub maintenance lock is not a regular file")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    async def publish(
        self,
        body: AsyncIterator[bytes],
        *,
        filename: str,
        slug: str,
        media_type: str,
        ttl_seconds: int,
        plan_name: str | None = None,
    ) -> Artifact:
        async with self.upload_slots:
            return await self._publish(
                body,
                filename=filename,
                slug=slug,
                media_type=media_type,
                ttl_seconds=ttl_seconds,
                plan_name=plan_name,
            )

    async def _publish(
        self,
        body: AsyncIterator[bytes],
        *,
        filename: str,
        slug: str,
        media_type: str,
        ttl_seconds: int,
        plan_name: str | None,
    ) -> Artifact:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="upload-", suffix=".part", dir=self.config.incoming_dir
        )
        temporary_path = Path(temporary_name)
        os.chmod(temporary_path, 0o600)
        size = 0
        digest = hashlib.sha256()
        artifact_id = secrets.token_urlsafe(24)
        final_directory = self.object_directory(artifact_id)
        final_path = final_directory / "payload"
        moved = False
        output = os.fdopen(descriptor, "wb", closefd=True)
        fcntl.flock(output.fileno(), fcntl.LOCK_EX)

        try:
            async for chunk in body:
                if not chunk:
                    continue
                size += len(chunk)
                if size > self.config.max_artifact_bytes:
                    raise ArtifactTooLarge
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())

            now = int(time.time())
            artifact = Artifact(
                id=artifact_id,
                filename=filename,
                slug=slug,
                media_type=media_type,
                size_bytes=size,
                sha256=digest.hexdigest(),
                created_at=now,
                expires_at=now + ttl_seconds,
            )

            with self.database.transaction(immediate=True) as connection:
                if self.database.total_size(connection) + size > self.config.storage_quota_bytes:
                    raise StorageQuotaExceeded
                prefix_directory = final_directory.parent
                prefix_was_new = not prefix_directory.exists()
                final_directory.mkdir(parents=True, mode=0o700)
                final_directory.chmod(0o700)
                os.replace(temporary_path, final_path)
                moved = True
                os.chmod(final_path, 0o600)
                fsync_directory(final_directory)
                fsync_directory(prefix_directory)
                if prefix_was_new:
                    fsync_directory(self.config.objects_dir)
                fsync_directory(self.config.incoming_dir)
                self.database.insert(connection, artifact)
                if plan_name is not None:
                    self.database.set_plan(connection, plan_name, artifact.id, now)

            return artifact
        except Exception:
            if moved:
                shutil.rmtree(final_directory, ignore_errors=True)
                fsync_directory(final_directory.parent)
            else:
                temporary_path.unlink(missing_ok=True)
            raise
        finally:
            output.close()

    def open_payload(self, artifact_id: str):
        path = self.object_path(artifact_id)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise ArtifactMissing from exc
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            os.close(descriptor)
            raise ArtifactMissing
        return os.fdopen(descriptor, "rb", closefd=True)

    def garbage_collect(
        self, now: int | None = None, stale_after_seconds: int = 7200
    ) -> dict[str, int]:
        with self.maintenance_lock():
            return self._garbage_collect(now=now, stale_after_seconds=stale_after_seconds)

    def _garbage_collect(
        self, now: int | None = None, stale_after_seconds: int = 7200
    ) -> dict[str, int]:
        now = int(time.time()) if now is None else now
        moved: list[tuple[Path, Path]] = []
        expired_count = 0

        try:
            with self.database.transaction(immediate=True) as connection:
                for artifact in self.database.expired(connection, now):
                    source = self.object_directory(artifact.id)
                    trash = self.config.trash_dir / f"{artifact.id}-{secrets.token_hex(6)}"
                    if source.exists():
                        os.replace(source, trash)
                        fsync_directory(source.parent)
                        fsync_directory(self.config.trash_dir)
                        moved.append((source, trash))
                    self.database.delete(connection, artifact.id)
                    expired_count += 1
        except Exception:
            for source, trash in reversed(moved):
                if trash.exists() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    os.replace(trash, source)
            raise

        for _, trash in moved:
            shutil.rmtree(trash, ignore_errors=True)
        if moved:
            fsync_directory(self.config.trash_dir)

        stale_before = now - stale_after_seconds
        stale_parts = 0
        for entry in self.config.incoming_dir.glob("*.part"):
            descriptor = None
            try:
                descriptor = os.open(entry, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if os.fstat(descriptor).st_mtime <= stale_before:
                    entry.unlink()
                    fsync_directory(self.config.incoming_dir)
                    stale_parts += 1
            except (FileNotFoundError, BlockingIOError):
                pass
            finally:
                if descriptor is not None:
                    os.close(descriptor)

        referenced = self.database.all_ids()
        orphaned = 0
        missing = 0
        for prefix in self.config.objects_dir.iterdir():
            if not prefix.is_dir() or prefix.is_symlink():
                continue
            for object_dir in prefix.iterdir():
                if not object_dir.is_dir() or object_dir.is_symlink():
                    continue
                artifact_id = object_dir.name
                try:
                    old_enough = object_dir.stat().st_mtime <= stale_before
                except FileNotFoundError:
                    continue
                if artifact_id not in referenced and old_enough:
                    shutil.rmtree(object_dir, ignore_errors=True)
                    orphaned += 1

        for artifact_id in list(referenced):
            if not self.object_path(artifact_id).is_file():
                with self.database.transaction(immediate=True) as connection:
                    self.database.delete(connection, artifact_id)
                missing += 1

        for entry in self.config.trash_dir.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            elif entry.is_file() and not entry.is_symlink():
                entry.unlink(missing_ok=True)

        with self.database.connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)")

        return {
            "expired": expired_count,
            "stale_parts": stale_parts,
            "orphaned": orphaned,
            "missing": missing,
        }
