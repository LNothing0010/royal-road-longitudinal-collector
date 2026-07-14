from __future__ import annotations

import csv
import json
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings

EXPORT_QUERIES = {
    "runs.csv": "SELECT * FROM run ORDER BY run_id",
    "fictions.csv": "SELECT * FROM fiction ORDER BY first_seen_utc,fiction_id",
    "source_snapshots.csv": "SELECT * FROM source_snapshot ORDER BY run_id,source_name",
    "listing_memberships.csv": "SELECT * FROM listing_membership ORDER BY run_id,source_name,rank",
    "metric_observations.csv": "SELECT * FROM metric_observation ORDER BY run_id,fiction_id,source_name",
    "metadata_observations.csv": "SELECT * FROM metadata_observation ORDER BY run_id,fiction_id,source_name",
    "release_events.csv": "SELECT * FROM release_event ORDER BY fiction_id,published_utc",
    "list_transitions.csv": "SELECT * FROM list_transition ORDER BY run_id,source_name,current_rank",
    "list_cutoffs.csv": "SELECT * FROM list_cutoff ORDER BY run_id,source_name",
    "metric_deltas.csv": "SELECT * FROM metric_delta ORDER BY run_id,fiction_id,horizon_hours",
    "interventions.csv": "SELECT * FROM intervention_event ORDER BY event_utc,event_id",
    "model_registry.csv": "SELECT * FROM model_registry ORDER BY created_utc",
    "methodology_changes.csv": "SELECT * FROM methodology_change ORDER BY change_id",
}


def export_archive(settings: Settings | None = None, latest_alias: bool = True) -> Path:
    settings = settings or Settings()
    export_dir = Path("data/export")
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    staging = export_dir / f"staging_{stamp}"
    staging.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.db_path) as conn:
        for filename, query in EXPORT_QUERIES.items():
            path = staging / filename
            cursor = conn.execute(query)
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow([description[0] for description in cursor.description])
                writer.writerows(cursor)
        run_counts = dict(conn.execute("SELECT status,COUNT(*) FROM run GROUP BY status").fetchall())
        manifest = {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "database": settings.db_path.name,
            "run_counts": run_counts,
            "latest_run": conn.execute("SELECT MAX(run_id) FROM run").fetchone()[0],
            "fiction_count": conn.execute("SELECT COUNT(*) FROM fiction").fetchone()[0],
            "membership_count": conn.execute("SELECT COUNT(*) FROM listing_membership").fetchone()[0],
            "metric_observation_count": conn.execute("SELECT COUNT(*) FROM metric_observation").fetchone()[0],
        }
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    shutil.copy2(settings.db_path, staging / settings.db_path.name)
    dictionary = Path("DATA_DICTIONARY.md")
    if dictionary.exists():
        shutil.copy2(dictionary, staging / dictionary.name)
    methodology = Path("METHODOLOGY.md")
    if methodology.exists():
        shutil.copy2(methodology, staging / methodology.name)
    if settings.report_dir.exists():
        reports_out = staging / "reports"
        reports_out.mkdir(exist_ok=True)
        for report in settings.report_dir.glob("*.json"):
            shutil.copy2(report, reports_out / report.name)
    raw_out = staging / "raw"
    raw_out.mkdir(exist_ok=True)
    if settings.raw_dir.exists():
        for raw in settings.raw_dir.rglob("*.json.gz"):
            destination = raw_out / raw.relative_to(settings.raw_dir)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(raw, destination)
    zip_path = export_dir / f"royal_road_longitudinal_{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in staging.rglob("*"):
            if file.is_file():
                archive.write(file, file.relative_to(staging))
    if latest_alias:
        shutil.copy2(zip_path, export_dir / "royal_road_longitudinal_latest.zip")
    shutil.rmtree(staging)
    return zip_path
