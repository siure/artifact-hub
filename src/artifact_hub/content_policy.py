from __future__ import annotations

import mimetypes
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

CONTROL_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn"}
MEDIA_TYPE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9!#$&^_.+\-]*/[a-zA-Z0-9][a-zA-Z0-9!#$&^_.+\-]*$")
SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")

INLINE_EXACT = {
    "application/json",
    "application/pdf",
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/svg+xml",
    "image/webp",
    "text/csv",
    "text/html",
    "text/markdown",
    "text/plain",
}

HTML_CSP = "; ".join(
    (
        "sandbox allow-scripts",
        "default-src 'none'",
        "script-src 'unsafe-inline' blob:",
        "style-src 'unsafe-inline'",
        "img-src data: blob:",
        "font-src data:",
        "media-src data: blob:",
        "connect-src 'none'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    )
)

SVG_CSP = "; ".join(
    (
        "sandbox",
        "default-src 'none'",
        "style-src 'unsafe-inline'",
        "img-src data:",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    )
)


class InvalidFilename(ValueError):
    pass


def normalize_filename(value: str) -> str:
    filename = unicodedata.normalize("NFC", value).strip()
    if not filename or filename in {".", ".."}:
        raise InvalidFilename("Filename is empty")
    if filename != Path(filename).name or "/" in filename or "\\" in filename:
        raise InvalidFilename("Filename must not contain path components")
    if any(unicodedata.category(char) in CONTROL_CATEGORIES for char in filename):
        raise InvalidFilename("Filename contains unsupported control characters")
    if len(filename.encode("utf-8")) > 180:
        raise InvalidFilename("Filename exceeds 180 UTF-8 bytes")
    return filename


def make_slug(filename: str) -> str:
    ascii_name = (
        unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    )
    slug = SLUG_RE.sub("-", ascii_name).strip("-.")
    if not slug:
        return "artifact"
    return slug[:180]


def normalize_media_type(value: str | None) -> str:
    media_type = (value or "application/octet-stream").split(";", 1)[0].strip().lower()
    if not MEDIA_TYPE_RE.fullmatch(media_type):
        raise ValueError("Invalid Content-Type")
    return media_type


def media_type_for_upload(filename: str, declared: str | None) -> str:
    """Use an explicit media type, otherwise infer one from the upload filename."""
    if declared:
        normalized = normalize_media_type(declared)
        if normalized not in {
            "application/octet-stream",
            "application/x-www-form-urlencoded",
        }:
            return normalized
    inferred, _ = mimetypes.guess_type(filename, strict=False)
    return normalize_media_type(inferred)


def disposition_for(media_type: str) -> str:
    return "inline" if media_type in INLINE_EXACT else "attachment"


def content_disposition(media_type: str, filename: str) -> str:
    fallback = "".join(
        char if char.isascii() and (char.isalnum() or char in " ._-()") else "_"
        for char in filename
    ).strip()
    fallback = fallback.replace('"', "_") or "artifact"
    encoded = quote(filename, safe="")
    return f'{disposition_for(media_type)}; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'


def artifact_headers(media_type: str, filename: str) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        "X-Frame-Options": "DENY",
        "Cache-Control": "private, no-cache",
        "Content-Disposition": content_disposition(media_type, filename),
    }
    if media_type == "text/html":
        headers["Content-Security-Policy"] = HTML_CSP
    elif media_type == "image/svg+xml":
        headers["Content-Security-Policy"] = SVG_CSP
    return headers
