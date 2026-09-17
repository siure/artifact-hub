from __future__ import annotations

import argparse
import json

import uvicorn

from .app import create_app
from .config import Config
from .database import Database
from .storage import Storage


def command_serve(args: argparse.Namespace) -> int:
    config = Config.from_env()
    if args.host is not None or args.port is not None:
        config = Config(
            **{
                **{field: getattr(config, field) for field in config.__dataclass_fields__},
                "host": args.host or config.host,
                "port": args.port or config.port,
            }
        )
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        workers=1,
        access_log=False,
        server_header=False,
    )
    return 0


def command_init_db(_: argparse.Namespace) -> int:
    config = Config.from_env()
    config.ensure_directories()
    Database(config.database_path).initialize()
    return 0


def command_gc(_: argparse.Namespace) -> int:
    config = Config.from_env()
    config.ensure_directories()
    database = Database(config.database_path)
    database.initialize()
    result = Storage(config, database).garbage_collect()
    print(json.dumps(result, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="artifact-hub")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the Artifact Hub server")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.set_defaults(function=command_serve)

    init_db = subparsers.add_parser("init-db", help="Initialize the metadata database")
    init_db.set_defaults(function=command_init_db)

    gc = subparsers.add_parser("gc", help="Delete expired and orphaned artifacts")
    gc.set_defaults(function=command_gc)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(args.function(args))


if __name__ == "__main__":
    main()
