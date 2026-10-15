"""Competence, the skills-matrix app, on this machine only.

    competence                    start the app on the real database and open it in the browser
    competence --demo             the same on the demo database (invented people)
    competence init               create the real database from the data-team catalogue
    competence init --catalogue empty|dev|FILE
    competence init --demo        create the demo database

Databases live in the per-user app folder (a source checkout uses its data/ folder); --data DIR
picks another. On every start the database is backed up (newest 20 kept), then migrated.
"""

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from competence import seed
from competence.core import catalogue as cat
from competence.core import paths
from competence.core.backup import prepare_database
from competence.web.app import ENV_BACKUPS, ENV_DB, ENV_DEMO, create_app

HOST = "127.0.0.1"  # never 0.0.0.0: other machines must not reach this app
PACKAGE = Path(__file__).resolve().parent


def run(args: argparse.Namespace) -> int:
    db = paths.demo_db() if args.demo else paths.real_db()
    try:
        backup = prepare_database(db, paths.backups())
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    url = f"http://localhost:{args.port}"
    print(f"Backed up to {backup}")
    print(f"Open {url}  (Ctrl+C to stop)")
    if not args.no_browser and not args.reload:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    if args.reload:
        # The reloader imports the app afresh in a child process; only core/ and web/ are watched, never data.
        os.environ.update({ENV_DB: str(db), ENV_DEMO: "1" if args.demo else "0", ENV_BACKUPS: str(paths.backups())})
        uvicorn.run(
            "competence.web.app:create_app_from_env",
            factory=True,
            host=HOST,
            port=args.port,
            reload=True,
            reload_dirs=[str(PACKAGE / "core"), str(PACKAGE / "web")],
        )
    else:
        uvicorn.run(create_app(db, demo=args.demo, backups=paths.backups()), host=HOST, port=args.port)
    return 0


def init(args: argparse.Namespace) -> int:
    path = args.db or (paths.demo_db() if args.demo else paths.real_db())
    source = cat.find(args.catalogue) if args.catalogue else (cat.DEMO if args.demo else cat.DEFAULT)
    try:
        loaded = seed.create_database(path, demo=args.demo, catalogue=source)
    except (FileExistsError, ValueError) as e:  # CatalogueError is a ValueError
        print(e, file=sys.stderr)
        return 1
    print(
        f"created {path}{' (demo)' if args.demo else ''} from {source.name}:"
        f" {len(loaded.skills)} skills in {len(loaded.categories)} categories, {len(loaded.roles)} roles"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="competence", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--data", type=Path, help="folder for databases and backups (default: the per-user app folder)")
    ap.add_argument("--demo", action="store_true", help="use the demo database instead of the real one")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    ap.add_argument("--reload", action="store_true", help="restart when code changes (development only)")
    sub = ap.add_subparsers(dest="command")
    ip = sub.add_parser("init", help="create a new database (never overwrites one)")
    # SUPPRESS: without it the subcommand's default would undo `competence --demo init`
    ip.add_argument(
        "--demo", action="store_true", default=argparse.SUPPRESS, help="create the demo database with invented people"
    )
    ip.add_argument(
        "--catalogue",
        metavar="NAME|FILE",
        help=f"catalogue to start from: {', '.join(cat.SHIPPED)} or a TOML file (default: data;"
        " the demo always uses dev)",
    )
    ip.add_argument("--db", type=Path, help="database file to create (default: matrix.sqlite or demo.sqlite)")
    args = ap.parse_args(argv)
    if args.data:
        os.environ[paths.ENV_DATA] = str(args.data.resolve())  # also reaches the --reload child process
    return init(args) if args.command == "init" else run(args)


if __name__ == "__main__":
    sys.exit(main())
