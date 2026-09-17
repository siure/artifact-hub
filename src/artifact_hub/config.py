from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MIB = 1024 * 1024
GIB = 1024 * MIB


def _path_env(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, str(default))).expanduser().resolve()


def _int_env(name: str, default: int) -> int:
    value = int(os.environ.get(name, default))
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


@dataclass(frozen=True, slots=True)
class Config:
    data_dir: Path
    objects_dir: Path
    incoming_dir: Path
    trash_dir: Path
    database_path: Path
    token_path: Path
    public_base_url: str
    host: str
    port: int
    default_ttl_seconds: int
    max_artifact_bytes: int
    storage_quota_bytes: int
    max_concurrent_uploads: int

    @classmethod
    def from_env(cls) -> "Config":
        home = Path.home()
        data_dir = _path_env(
            "ARTIFACT_HUB_DATA_DIR", home / ".local" / "share" / "artifact-hub"
        )
        config_dir = _path_env(
            "ARTIFACT_HUB_CONFIG_DIR", home / ".config" / "artifact-hub"
        )
        port = _int_env("ARTIFACT_HUB_PORT", 8787)
        base_url = os.environ.get(
            "ARTIFACT_HUB_PUBLIC_BASE_URL", f"http://127.0.0.1:{port}"
        ).rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("ARTIFACT_HUB_PUBLIC_BASE_URL must be an HTTP(S) URL")

        return cls(
            data_dir=data_dir,
            objects_dir=data_dir / "objects",
            incoming_dir=data_dir / "incoming",
            trash_dir=data_dir / "trash",
            database_path=_path_env("ARTIFACT_HUB_DATABASE", data_dir / "metadata.sqlite3"),
            token_path=_path_env("ARTIFACT_HUB_TOKEN_FILE", config_dir / "publish.token"),
            public_base_url=base_url,
            host=os.environ.get("ARTIFACT_HUB_HOST", "127.0.0.1"),
            port=port,
            default_ttl_seconds=_int_env("ARTIFACT_HUB_DEFAULT_TTL", 30 * 24 * 3600),
            max_artifact_bytes=_int_env("ARTIFACT_HUB_MAX_BYTES", 512 * MIB),
            storage_quota_bytes=_int_env("ARTIFACT_HUB_QUOTA_BYTES", 25 * GIB),
            max_concurrent_uploads=max(1, _int_env("ARTIFACT_HUB_MAX_UPLOADS", 2)),
        )

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.objects_dir,
            self.incoming_dir,
            self.trash_dir,
            self.database_path.parent,
            self.token_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.chmod(0o700)
