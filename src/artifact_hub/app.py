from __future__ import annotations

import email.utils
import time
from contextlib import asynccontextmanager
from typing import Iterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

from .auth import AuthenticationError, load_token, require_bearer
from .config import Config
from .content_policy import (
    InvalidFilename,
    artifact_headers,
    make_slug,
    media_type_for_upload,
    normalize_filename,
)
from .database import Database
from .storage import ArtifactMissing, ArtifactTooLarge, Storage, StorageQuotaExceeded


def error_response(status: int, code: str, message: str, headers: dict[str, str] | None = None):
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status, headers=headers
    )


def parse_range(value: str, size: int) -> tuple[int, int]:
    if not value.startswith("bytes=") or "," in value:
        raise ValueError
    spec = value[6:]
    if "-" not in spec:
        raise ValueError
    start_value, end_value = spec.split("-", 1)
    if not start_value:
        suffix = int(end_value)
        if suffix <= 0:
            raise ValueError
        start = max(0, size - suffix)
        end = size - 1
    else:
        start = int(start_value)
        end = size - 1 if not end_value else int(end_value)
    if size == 0 or start < 0 or end < start or start >= size:
        raise ValueError
    return start, min(end, size - 1)


def file_chunks(file_object, start: int, length: int, chunk_size: int = 256 * 1024) -> Iterator[bytes]:
    try:
        file_object.seek(start)
        remaining = length
        while remaining:
            chunk = file_object.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        file_object.close()


def create_app(config: Config | None = None) -> Starlette:
    config = config or Config.from_env()
    config.ensure_directories()
    database = Database(config.database_path)
    database.initialize()
    storage = Storage(config, database)
    token = load_token(config.token_path)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        app.state.config = config
        app.state.database = database
        app.state.storage = storage
        app.state.publish_token = token
        yield

    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def auth_check(request: Request) -> Response:
        try:
            require_bearer(request.headers.get("authorization"), token)
        except AuthenticationError:
            return error_response(401, "unauthorized", "A valid publisher token is required")
        return JSONResponse({"status": "ok"})

    async def publish_request(
        request: Request,
        *,
        raw_filename: str,
        media_type: str | None = None,
        plan_name: str | None = None,
    ) -> Response:
        try:
            require_bearer(request.headers.get("authorization"), token)
        except AuthenticationError:
            return error_response(
                401,
                "unauthorized",
                "A valid publisher token is required",
                {"WWW-Authenticate": "Bearer"},
            )

        try:
            filename = normalize_filename(raw_filename)
            resolved_media_type = media_type or media_type_for_upload(
                filename, request.headers.get("content-type")
            )
        except (InvalidFilename, ValueError) as exc:
            return error_response(400, "invalid_request", str(exc))

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                return error_response(400, "invalid_content_length", "Content-Length is invalid")
            if declared_size < 0:
                return error_response(400, "invalid_content_length", "Content-Length is invalid")
            if declared_size > config.max_artifact_bytes:
                return error_response(413, "artifact_too_large", "Artifact exceeds the size limit")

        try:
            artifact = await storage.publish(
                request.stream(),
                filename=filename,
                slug=make_slug(filename),
                media_type=resolved_media_type,
                ttl_seconds=config.default_ttl_seconds,
                plan_name=plan_name,
            )
        except ArtifactTooLarge:
            return error_response(413, "artifact_too_large", "Artifact exceeds the size limit")
        except StorageQuotaExceeded:
            return error_response(507, "storage_quota_exceeded", "Artifact storage quota is full")
        except OSError:
            return error_response(507, "storage_error", "Artifact storage is unavailable")

        if plan_name is None:
            url = f"{config.public_base_url}/a/{artifact.id}/{artifact.slug}"
        else:
            url = f"{config.public_base_url}/p/{plan_name}"
        return PlainTextResponse(url + "\n", status_code=201)

    async def publish(request: Request) -> Response:
        return await publish_request(request, raw_filename=request.path_params["filename"])

    async def publish_plan(request: Request) -> Response:
        plan_name = request.path_params["plan_name"]
        if not plan_name.endswith(".html"):
            return error_response(400, "invalid_plan_name", "Plan name must end with .html")
        return await publish_request(
            request,
            raw_filename=plan_name,
            media_type="text/html",
            plan_name=plan_name,
        )

    async def retrieve(request: Request) -> Response:
        artifact_id = request.path_params["artifact_id"]
        slug = request.path_params["slug"]
        if not storage.valid_id(artifact_id):
            return error_response(404, "not_found", "Artifact not found")
        artifact = database.get(artifact_id)
        if artifact is None or artifact.slug != slug or artifact.expires_at <= int(time.time()):
            return error_response(404, "not_found", "Artifact not found")

        return serve_artifact(request, artifact)

    async def retrieve_plan(request: Request) -> Response:
        plan_name = request.path_params["plan_name"]
        try:
            plan_name = normalize_filename(plan_name)
        except InvalidFilename:
            return error_response(404, "not_found", "Plan not found")
        artifact = database.get_plan(plan_name)
        if artifact is None or artifact.expires_at <= int(time.time()):
            return error_response(404, "not_found", "Plan not found")
        return serve_artifact(request, artifact)

    def serve_artifact(request: Request, artifact) -> Response:
        try:
            payload = storage.open_payload(artifact.id)
        except ArtifactMissing:
            return error_response(404, "not_found", "Artifact not found")

        headers = artifact_headers(artifact.media_type, artifact.filename)
        headers.update(
            {
                "Accept-Ranges": "bytes",
                "ETag": f'"sha256-{artifact.sha256}"',
                "Last-Modified": email.utils.formatdate(artifact.created_at, usegmt=True),
            }
        )

        if request.headers.get("if-none-match") == headers["ETag"]:
            payload.close()
            return Response(status_code=304, headers=headers)

        start = 0
        end = artifact.size_bytes - 1
        status_code = 200
        range_value = request.headers.get("range") if request.method == "GET" else None
        if_range = request.headers.get("if-range")
        if if_range and if_range not in {headers["ETag"], headers["Last-Modified"]}:
            range_value = None
        if range_value:
            try:
                start, end = parse_range(range_value, artifact.size_bytes)
            except (ValueError, TypeError):
                payload.close()
                headers["Content-Range"] = f"bytes */{artifact.size_bytes}"
                return Response(status_code=416, headers=headers)
            status_code = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{artifact.size_bytes}"

        length = 0 if artifact.size_bytes == 0 else end - start + 1
        headers["Content-Length"] = str(length)
        if request.method == "HEAD":
            payload.close()
            return Response(status_code=status_code, headers=headers, media_type=artifact.media_type)
        return StreamingResponse(
            file_chunks(payload, start, length),
            status_code=status_code,
            headers=headers,
            media_type=artifact.media_type,
        )

    routes = [
        Route("/v1/auth/check", auth_check, methods=["GET"]),
        Route("/healthz", health, methods=["GET"]),
        Route("/v1/artifacts/{filename}", publish, methods=["POST"]),
        Route("/v1/plans/{plan_name}", publish_plan, methods=["PUT"]),
        Route("/a/{artifact_id}/{slug}", retrieve, methods=["GET", "HEAD"]),
        Route("/p/{plan_name}", retrieve_plan, methods=["GET", "HEAD"]),
    ]
    return Starlette(routes=routes, lifespan=lifespan)
