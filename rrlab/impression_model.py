from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any


TRAFFIC_COLUMNS = (
    "provider",
    "target_url",
    "scope",
    "granularity",
    "period_start_utc",
    "period_end_utc",
    "visits",
)
PAGE_TARGET = "royalroad.com/fictions/latest-updates"
DOMAIN_TARGET = "royalroad.com"


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_target(value: str) -> str:
    return value.strip().lower().replace("https://", "").replace("http://", "").rstrip("/")


def load_external_traffic(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in TRAFFIC_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"external traffic CSV missing columns: {','.join(missing)}")
        for line_number, row in enumerate(reader, start=2):
            try:
                start = _parse_utc(str(row["period_start_utc"]))
                end = _parse_utc(str(row["period_end_utc"]))
                visits = float(str(row["visits"]).replace(",", ""))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid external traffic row {line_number}: {exc}") from exc
            provider = str(row["provider"]).strip().lower()
            scope = str(row["scope"]).strip().lower()
            granularity = str(row["granularity"]).strip().lower()
            target = _normalize_target(str(row["target_url"]))
            if not provider or scope not in {"page", "domain", "subfolder"}:
                raise ValueError(f"invalid provider/scope at row {line_number}")
            if granularity not in {"hour", "day", "month"}:
                raise ValueError(f"invalid granularity at row {line_number}")
            if end <= start or visits < 0:
                raise ValueError(f"invalid interval/visits at row {line_number}")
            rows.append(
                {
                    "provider": provider,
                    "target_url": target,
                    "scope": scope,
                    "granularity": granularity,
                    "start": start,
                    "end": end,
                    "visits": visits,
                }
            )
    return rows


def _overlap_minutes(start: datetime, end: datetime, row: dict[str, Any]) -> float:
    overlap_start = max(start, row["start"])
    overlap_end = min(end, row["end"])
    return max(0.0, (overlap_end - overlap_start).total_seconds() / 60)


def _direct_page_hour_estimates(
    rows: list[dict[str, Any]], start: datetime, end: datetime
) -> dict[str, float]:
    estimates: dict[str, float] = {}
    for row in rows:
        if row["scope"] != "page" or row["granularity"] != "hour":
            continue
        if _normalize_target(row["target_url"]) != PAGE_TARGET:
            continue
        overlap = _overlap_minutes(start, end, row)
        if overlap <= 0:
            continue
        interval_minutes = (row["end"] - row["start"]).total_seconds() / 60
        estimates[row["provider"]] = estimates.get(row["provider"], 0.0) + (
            row["visits"] * overlap / interval_minutes
        )
    return estimates


def _provider_proxy_estimate(
    rows: list[dict[str, Any]], provider: str, start: datetime, end: datetime
) -> float | None:
    provider_rows = [row for row in rows if row["provider"] == provider]
    page_baselines = [
        row
        for row in provider_rows
        if row["scope"] == "page"
        and _normalize_target(row["target_url"]) == PAGE_TARGET
        and row["granularity"] in {"day", "month"}
        and row["start"] <= start < row["end"]
    ]
    if not page_baselines:
        return None
    page_baseline = min(
        page_baselines,
        key=lambda row: (row["end"] - row["start"]).total_seconds(),
    )
    domain_baselines = [
        row
        for row in provider_rows
        if row["scope"] == "domain"
        and _normalize_target(row["target_url"]) == DOMAIN_TARGET
        and row["start"] == page_baseline["start"]
        and row["end"] == page_baseline["end"]
        and row["visits"] > 0
    ]
    if not domain_baselines:
        return None
    domain_baseline = domain_baselines[0]
    page_share = page_baseline["visits"] / domain_baseline["visits"]
    hourly_domain = [
        row
        for row in provider_rows
        if row["scope"] == "domain"
        and _normalize_target(row["target_url"]) == DOMAIN_TARGET
        and row["granularity"] == "hour"
    ]
    if not hourly_domain:
        return None
    estimate = 0.0
    covered = 0.0
    episode_minutes = (end - start).total_seconds() / 60
    for row in hourly_domain:
        overlap = _overlap_minutes(start, end, row)
        if overlap <= 0:
            continue
        interval_minutes = (row["end"] - row["start"]).total_seconds() / 60
        estimate += row["visits"] * page_share * overlap / interval_minutes
        covered += overlap
    if covered + 1e-9 < episode_minutes:
        return None
    return estimate


def estimate_page_visits(
    rows: list[dict[str, Any]], start: datetime, end: datetime
) -> dict[str, Any]:
    estimates = _direct_page_hour_estimates(rows, start, end)
    providers = sorted({row["provider"] for row in rows})
    for provider in providers:
        if provider in estimates:
            continue
        estimate = _provider_proxy_estimate(rows, provider, start, end)
        if estimate is not None:
            estimates[provider] = estimate
    values = list(estimates.values())
    return {
        "estimated_page_visits": round(median(values), 6) if values else None,
        "provider_estimates": {
            provider: round(value, 6) for provider, value in sorted(estimates.items())
        },
        "provider_count": len(values),
        "estimate_min": round(min(values), 6) if values else None,
        "estimate_max": round(max(values), 6) if values else None,
        "method": (
            "direct_page_hourly"
            if any(
                row["scope"] == "page" and row["granularity"] == "hour"
                for row in rows
            )
            else "page_baseline_times_domain_hourly_share"
            if values
            else "unavailable"
        ),
    }


def _traction_map(exposure_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row["fiction_id"]): row
        for row in exposure_report.get("exposure_traction", [])
    }


def page_visit_opportunity(
    exposure_report: dict[str, Any], traffic_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    traction = _traction_map(exposure_report)
    episodes: list[dict[str, Any]] = []
    for source_name, surface in exposure_report.get("surfaces", {}).items():
        if source_name not in {"latest_updates_live", "home_latest_updates"}:
            continue
        for episode in surface.get("episodes", []):
            start = _parse_utc(str(episode["first_seen_utc"]))
            exit_upper = episode.get("exit_upper_utc")
            end = _parse_utc(str(exit_upper or episode["last_seen_utc"]))
            if end <= start:
                continue
            estimate = estimate_page_visits(traffic_rows, start, end)
            outcome = traction.get(str(episode["fiction_id"]), {})
            view_delta = outcome.get("view_delta")
            visits = estimate["estimated_page_visits"]
            views_per_1000 = None
            if visits is not None and visits > 0 and view_delta is not None:
                views_per_1000 = 1000 * float(view_delta) / float(visits)
            rank = episode.get("median_rank")
            episodes.append(
                {
                    "source_name": source_name,
                    "fiction_id": episode["fiction_id"],
                    "title": episode["title"],
                    "author": episode.get("author"),
                    "url": episode.get("url"),
                    "first_seen_utc": episode["first_seen_utc"],
                    "exit_upper_utc": exit_upper,
                    "residence_estimated_minutes": episode.get(
                        "residence_estimated_minutes"
                    ),
                    "best_rank": episode.get("best_rank"),
                    "median_rank": rank,
                    "top5_during_episode": rank is not None and float(rank) <= 5,
                    "top10_during_episode": rank is not None and float(rank) <= 10,
                    **estimate,
                    "view_delta": view_delta,
                    "views_per_1000_estimated_page_visits": (
                        round(views_per_1000, 6) if views_per_1000 is not None else None
                    ),
                }
            )
    calibrated = [row for row in episodes if row["estimated_page_visits"] is not None]
    return {
        "status": "ready" if calibrated else "uncalibrated",
        "generated_utc": _utc_text(datetime.now(timezone.utc)),
        "methodology": {
            "impression_definition": (
                "A potential impression is a visit to Royal Road's official Latest Updates "
                "page while the fiction is present."
            ),
            "page_traffic_is_input": True,
            "novel_views_used_to_estimate_page_traffic": False,
            "novel_views_used_as_outcome": True,
            "rank_handling": (
                "Page visits are reported separately from top-5/top-10 membership; no "
                "unsupported scroll-depth probability is invented."
            ),
            "causal_claim": False,
        },
        "traffic_rows": len(traffic_rows),
        "calibrated_episodes": len(calibrated),
        "episodes": episodes,
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Latest Updates page-visit opportunity",
        "",
        f"- Status: `{report['status']}`",
        "- Page traffic is supplied by external traffic-estimation data.",
        "- Novel views are outcomes, never inputs to page-traffic estimation.",
        "",
        "| Fiction | Surface | Residence min | Est. page visits | Providers | View delta | Views / 1k page visits |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["episodes"][:100]:
        lines.append(
            "| {title} | {source} | {residence} | {visits} | {providers} | {views} | {rate} |".format(
                title=str(row["title"]).replace("|", "\\|"),
                source=row["source_name"],
                residence=row.get("residence_estimated_minutes") or "—",
                visits=row.get("estimated_page_visits") or "—",
                providers=row.get("provider_count", 0),
                views=row.get("view_delta") if row.get("view_delta") is not None else "—",
                rate=(
                    row.get("views_per_1000_estimated_page_visits")
                    if row.get("views_per_1000_estimated_page_visits") is not None
                    else "—"
                ),
            )
        )
    if not report["episodes"]:
        lines.append("| — | — | — | — | — | — | — |")
    lines += [
        "",
        "> A monthly or daily page total alone cannot identify 12:00–13:00 versus "
        "18:00–19:00. An hourly page series, or an hourly domain series combined with "
        "a page/domain baseline from the same provider and period, is required.",
        "",
    ]
    return "\n".join(lines)


def write_impression_report(
    exposure_json_path: Path,
    traffic_csv_path: Path,
    report_dir: Path,
) -> dict[str, Any]:
    if not exposure_json_path.exists():
        raise FileNotFoundError(exposure_json_path)
    exposure = json.loads(exposure_json_path.read_text(encoding="utf-8"))
    traffic_rows = load_external_traffic(traffic_csv_path)
    report = page_visit_opportunity(exposure, traffic_rows)
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "page_visit_opportunity_latest.json"
    markdown_path = report_dir / "page_visit_opportunity_latest.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return {
        "status": report["status"],
        "traffic_rows": report["traffic_rows"],
        "calibrated_episodes": report["calibrated_episodes"],
        "episode_count": len(report["episodes"]),
        "files": {"json": str(json_path), "markdown": str(markdown_path)},
    }
