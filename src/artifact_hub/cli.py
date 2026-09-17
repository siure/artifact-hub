from __future__ import annotations

import argparse
import getpass
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
from urllib.parse import quote, urlsplit

from .auth import load_token


class ClientError(Exception):
    pass


def publisher_token(path: Path) -> str:
    try:
        token = load_token(path)
    except RuntimeError as exc:
        raise ClientError("Token file is missing or invalid; run artifact configure") from exc
    if any(character.isspace() for character in token):
        raise ClientError("Publisher token must not contain whitespace")
    return token


def config_directory() -> Path:
    return Path(os.environ.get("ARTIFACT_HUB_CONFIG_DIR", "~/.config/artifact-hub")).expanduser()


def parse_endpoint(endpoint: str):
    url = urlsplit(endpoint.rstrip("/"))
    if (
        url.scheme not in {"http", "https"} or not url.hostname
        or url.username is not None or url.password is not None
        or url.path or url.query or url.fragment
    ):
        raise ValueError("Endpoint must be an HTTP(S) origin without credentials or a path")
    if url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Remote endpoints require HTTPS")
    _ = url.port
    return url


def request(endpoint: str, token: str, method: str, path: str, body=None, size=None) -> str:
    url = parse_endpoint(endpoint)
    connection_type = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(url.hostname, url.port, timeout=120)
    headers = {"Authorization": f"Bearer {token}"}
    if size is not None:
        headers["Content-Length"] = str(size)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read(65536).decode("utf-8", errors="replace").strip()
        if response.status == 401:
            raise ClientError("Publisher authentication failed; run artifact configure")
        if response.status not in {200, 201}:
            # Do not echo response bodies: a proxy could include request credentials.
            raise ClientError(f"Artifact Hub returned HTTP {response.status}")
        if method != "GET":
            returned = urlsplit(payload)
            if returned.scheme not in {"http", "https"} or not returned.hostname or "\n" in payload:
                raise ClientError("Artifact Hub returned an invalid artifact URL")
        return payload
    finally:
        connection.close()


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".artifact-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(text)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def configure(args: argparse.Namespace) -> None:
    endpoint = args.endpoint.rstrip("/")
    parse_endpoint(endpoint)
    directory = config_directory()
    if args.token_file:
        token_path = Path(args.token_file).expanduser().resolve()
        token = publisher_token(token_path)
    else:
        if not sys.stdin.isatty():
            raise ClientError("Run configure in a terminal or pass --token-file with an existing file")
        token = getpass.getpass("Publisher token: ").strip()
        if len(token) < 32:
            raise ClientError("Publisher token is missing or too short")
        token_path = directory / "publish.token"
    if any(character.isspace() for character in token):
        raise ClientError("Publisher token must not contain whitespace")
    request(endpoint, token, "GET", "/v1/auth/check")
    if not args.token_file:
        write_private(token_path, token + "\n")
    stored_path = str(token_path) if args.token_file else "publish.token"
    write_private(
        directory / "client.toml",
        f"endpoint = {json.dumps(endpoint)}\ntoken_file = {json.dumps(stored_path)}\n",
    )
    print("Artifact publisher configured.")


def publish(args: argparse.Namespace) -> None:
    directory = config_directory()
    try:
        with (directory / "client.toml").open("rb") as source:
            config = tomllib.load(source)
        endpoint = config["endpoint"]
        if not isinstance(endpoint, str):
            raise TypeError
        token_path = Path(config["token_file"]).expanduser()
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise ClientError("Publisher configuration is missing or invalid; run artifact configure") from exc
    if not token_path.is_absolute():
        token_path = directory / token_path
    token = publisher_token(token_path)
    path = Path(args.file).expanduser()
    name = args.name if args.command == "plan" else path.name
    if not name or "/" in name or "\\" in name:
        raise ClientError("Publication name must be a filename")
    if args.command == "plan" and not name.endswith(".html"):
        raise ClientError("Plan name must end with .html")
    route = "/v1/plans/" if args.command == "plan" else "/v1/artifacts/"
    method = "PUT" if args.command == "plan" else "POST"
    if not path.is_file():
        raise ClientError("Upload path must be a regular file")
    with path.open("rb") as source:
        result = request(endpoint, token, method, route + quote(name, safe=""),
                         body=source, size=os.fstat(source.fileno()).st_size)
    print(result)


def main() -> None:
    parser = argparse.ArgumentParser(prog="artifact")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("configure", help="Configure this machine's publisher")
    setup.add_argument("--endpoint", required=True)
    setup.add_argument("--token-file", help="Reuse an existing token file without copying it")
    setup.set_defaults(function=configure)
    push = commands.add_parser("push", help="Publish an immutable file and print its URL")
    push.add_argument("file")
    push.set_defaults(function=publish)
    plan = commands.add_parser("plan", help="Publish HTML at a stable named URL")
    plan.add_argument("file")
    plan.add_argument("name")
    plan.set_defaults(function=publish)
    args = parser.parse_args()
    try:
        args.function(args)
    except ClientError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError, RuntimeError, http.client.HTTPException):
        # Keep network exceptions and credentials out of command output.
        print("Artifact command failed. Check configuration, file, credentials and server access.", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
