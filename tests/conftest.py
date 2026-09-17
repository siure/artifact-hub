from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest
from starlette.testclient import TestClient

from artifact_hub.app import create_app
from artifact_hub.config import Config

TOKEN = "test-token-with-more-than-thirty-two-characters"


@pytest.fixture
def config(tmp_path: Path) -> Config:
    data = tmp_path / "data"
    token = tmp_path / "config" / "publish.token"
    token.parent.mkdir(parents=True)
    token.write_text(TOKEN + "\n", encoding="utf-8")
    token.chmod(0o600)
    return Config(
        data_dir=data,
        objects_dir=data / "objects",
        incoming_dir=data / "incoming",
        trash_dir=data / "trash",
        database_path=data / "metadata.sqlite3",
        token_path=token,
        public_base_url="https://artifacts.example.test",
        host="127.0.0.1",
        port=8787,
        default_ttl_seconds=3600,
        max_artifact_bytes=1024,
        storage_quota_bytes=4096,
        max_concurrent_uploads=2,
    )


@pytest.fixture
def client(config: Config):
    with TestClient(create_app(config)) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def publish(client: TestClient, auth_headers: dict[str, str], content: bytes, **kwargs):
    filename = kwargs.pop("filename", "artifact.txt")
    media_type = kwargs.pop("media_type", None)
    headers = dict(auth_headers)
    if media_type is not None:
        headers["Content-Type"] = media_type
    return client.post(
        f"/v1/artifacts/{quote(filename, safe='')}",
        content=content,
        headers=headers,
        **kwargs,
    )


def publish_plan(
    client: TestClient,
    auth_headers: dict[str, str],
    content: bytes,
    name: str = "project-plan.html",
):
    return client.put(f"/v1/plans/{quote(name, safe='')}", content=content, headers=auth_headers)
