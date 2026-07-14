from __future__ import annotations

import argparse
import json

from .collector import run_collection
from .config import SOURCE_MAP, Settings
from .doctor import print_doctor
from .exporter import export_archive
from .queries import diagnostics_seed, fiction_history, latest_source, new_entrants
from .storage import Storage


def main() -> None:
    parser = argparse.ArgumentParser(prog="rrlab")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("init-db")
    collect = sub.add_parser("collect")
    collect.add_argument("--no-details", action="store_true")
    sub.add_parser("export")
    latest = sub.add_parser("latest")
    latest.add_argument("source_name", choices=sorted(SOURCE_MAP))
    entrants = sub.add_parser("entrants")
    entrants.add_argument("source_name", choices=sorted(SOURCE_MAP))
    history = sub.add_parser("history")
    history.add_argument("fiction_id")
    history.add_argument("--limit", type=int, default=500)
    diagnostics = sub.add_parser("diagnostics-seed")
    diagnostics.add_argument("--run-id", type=int)
    args = parser.parse_args()
    settings = Settings()

    if args.command == "doctor":
        print_doctor(settings)
    elif args.command == "init-db":
        Storage(settings.db_path, settings.raw_dir).init()
        print(settings.db_path)
    elif args.command == "collect":
        print(json.dumps(run_collection(settings, not args.no_details), indent=2, ensure_ascii=False))
    elif args.command == "export":
        print(export_archive(settings))
    elif args.command == "latest":
        print(json.dumps(latest_source(settings.db_path, args.source_name), indent=2, ensure_ascii=False))
    elif args.command == "entrants":
        print(json.dumps(new_entrants(settings.db_path, args.source_name), indent=2, ensure_ascii=False))
    elif args.command == "history":
        print(json.dumps(fiction_history(settings.db_path, args.fiction_id, args.limit), indent=2, ensure_ascii=False))
    elif args.command == "diagnostics-seed":
        print(json.dumps(diagnostics_seed(settings.db_path, args.run_id), indent=2, ensure_ascii=False))
