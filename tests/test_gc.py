from __future__ import annotations

import asyncio
import fcntl
import os
from pathlib import Path

from artifact_hub.database import Database
from artifact_hub.storage import Storage


async def body(content: bytes):
    yield content


def test_gc_removes_expired_and_stale_files(config):
    config.ensure_directories()
    database = Database(config.database_path)
    database.initialize()
    storage = Storage(config, database)

    artifact = asyncio.run(
        storage.publish(
            body(b"expired"),
            filename="expired.txt",
            slug="expired.txt",
            media_type="text/plain",
            ttl_seconds=1,
            plan_name="expired-plan.html",
        )
    )
    assert database.get_plan("expired-plan.html") == artifact
    stale = config.incoming_dir / "stale.part"
    stale.write_bytes(b"partial")
    stale.touch()

    result = storage.garbage_collect(now=artifact.expires_at + 7201, stale_after_seconds=7200)
    assert result["expired"] == 1
    assert result["stale_parts"] == 1
    assert database.get(artifact.id) is None
    assert database.get_plan("expired-plan.html") is None
    assert not storage.object_directory(artifact.id).exists()
    assert not stale.exists()


def test_gc_removes_old_orphan(config):
    config.ensure_directories()
    database = Database(config.database_path)
    database.initialize()
    storage = Storage(config, database)
    orphan = config.objects_dir / "ab" / ("a" * 32)
    orphan.mkdir(parents=True)
    (orphan / "payload").write_bytes(b"orphan")
    old = 10
    for path in (orphan, orphan / "payload"):
        path.touch()
        Path(path).chmod(0o700 if path.is_dir() else 0o600)
    os.utime(orphan, (old, old))
    result = storage.garbage_collect(now=10000, stale_after_seconds=100)
    assert result["orphaned"] == 1
    assert not orphan.exists()


def test_gc_does_not_remove_locked_upload(config):
    config.ensure_directories()
    database = Database(config.database_path)
    database.initialize()
    storage = Storage(config, database)
    active = config.incoming_dir / "active.part"
    with active.open("wb") as output:
        output.write(b"still uploading")
        output.flush()
        fcntl.flock(output.fileno(), fcntl.LOCK_EX)
        os.utime(active, (10, 10))
        result = storage.garbage_collect(now=10000, stale_after_seconds=100)
        assert result["stale_parts"] == 0
        assert active.exists()
