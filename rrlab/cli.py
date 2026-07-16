from __future__ import annotations

import argparse
import json
from pathlib import Path

from .catalog import catalog_status, run_catalog_backfill
from .collector import run_collection
from .config import SOURCE_MAP, Settings
from .doctor import print_doctor
from .exporter import export_archive
from .exposure import run_exposure_collection, write_exposure_analysis
from .impression_model import write_impression_report
from .launch_analysis import write_launch_analysis
from .longitudinal import run_longitudinal_refresh
from .queries import diagnostics_seed, fiction_history, latest_source, new_entrants
from .storage import Storage
from .validation import validate_latest


def main() -> None:
    parser = argparse.ArgumentParser(prog="rrlab")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("init-db")
    collect = sub.add_parser("collect")
    collect.add_argument("--no-details", action="store_true")
    sub.add_parser("collect-exposure")
    longitudinal = sub.add_parser("refresh-longitudinal")
    longitudinal.add_argument(
        "--max-fictions",
        type=int,
        default=None,
        help="Optional cap; omitted or 0 refreshes the entire tracked cohort.",
    )
    longitudinal.add_argument(
        "--analysis-only",
        action="store_true",
        help="Recompute and persist analysis without network detail requests.",
    )
    sub.add_parser("backfill-catalog")
    sub.add_parser("catalog-status")
    analyze = sub.add_parser("analyze-launches")
    analyze.add_argument("--run-id", type=int)
    analyze.add_argument("--lookback-hours", type=int, default=168)
    exposure = sub.add_parser("analyze-exposure")
    exposure.add_argument("--lookback-hours", type=int, default=168)
    page_visits = sub.add_parser("estimate-page-visit-opportunity")
    page_visits.add_argument(
        "--exposure-json",
        default="reports/exposure_analysis_latest.json",
    )
    page_visits.add_argument(
        "--traffic-csv",
        default="data/external_page_traffic.csv",
    )
    sub.add_parser("export")
    sub.add_parser("validate-latest")
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
        print(
            json.dumps(
                run_collection(settings, not args.no_details),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.command == "collect-exposure":
        print(
            json.dumps(
                run_exposure_collection(settings),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.command == "refresh-longitudinal":
        print(
            json.dumps(
                run_longitudinal_refresh(
                    settings,
                    max_fictions=args.max_fictions,
                    analysis_only=args.analysis_only,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.command == "backfill-catalog":
        print(json.dumps(run_catalog_backfill(settings), indent=2, ensure_ascii=False))
    elif args.command == "catalog-status":
        print(json.dumps(catalog_status(settings), indent=2, ensure_ascii=False))
    elif args.command == "analyze-launches":
        output = write_launch_analysis(
            settings.db_path,
            settings.report_dir,
            args.run_id,
            lookback_hours=args.lookback_hours,
        )
        print(json.dumps(output, indent=2, ensure_ascii=False))
    elif args.command == "analyze-exposure":
        output = write_exposure_analysis(
            settings.db_path,
            settings.report_dir,
            lookback_hours=args.lookback_hours,
        )
        print(json.dumps(output, indent=2, ensure_ascii=False))
    elif args.command == "estimate-page-visit-opportunity":
        output = write_impression_report(
            Path(args.exposure_json),
            Path(args.traffic_csv),
            settings.report_dir,
        )
        print(json.dumps(output, indent=2, ensure_ascii=False))
    elif args.command == "export":
        print(export_archive(settings))
    elif args.command == "validate-latest":
        path = validate_latest(settings.db_path, settings.report_dir)
        report = json.loads(path.read_text(encoding="utf-8"))
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if not report.get("valid_for_complete_rs_analysis", False):
            raise SystemExit(1)
    elif args.command == "latest":
        print(
            json.dumps(
                latest_source(settings.db_path, args.source_name),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.command == "entrants":
        print(
            json.dumps(
                new_entrants(settings.db_path, args.source_name),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.command == "history":
        print(
            json.dumps(
                fiction_history(settings.db_path, args.fiction_id, args.limit),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.command == "diagnostics-seed":
        print(
            json.dumps(
                diagnostics_seed(settings.db_path, args.run_id),
                indent=2,
                ensure_ascii=False,
            )
        )
