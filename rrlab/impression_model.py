from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any


TRAFFIC_COLUMNS = (
    "observed_utc",
    "campaign_id",
    "ad_format",
    "cumulative_impressions",
)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _median_or_none(values: list[float]) -> float | None:
    return median(values) if values else None


def load_probe_snapshots(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in TRAFFIC_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"traffic probe CSV missing columns: {','.join(missing)}")
        for line_number, row in enumerate(reader, start=2):
            try:
                observed = _parse_utc(str(row["observed_utc"]))
                cumulative = int(str(row["cumulative_impressions"]).replace(",", ""))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid traffic probe row {line_number}: {exc}") from exc
            if cumulative < 0:
                raise ValueError(f"negative cumulative impressions at row {line_number}")
            campaign_id = str(row["campaign_id"]).strip()
            ad_format = str(row["ad_format"]).strip().lower()
            if not campaign_id or not ad_format:
                raise ValueError(f"empty campaign_id/ad_format at row {line_number}")
            rows.append(
                {
                    "observed_utc": observed,
                    "campaign_id": campaign_id,
                    "ad_format": ad_format,
                    "cumulative_impressions": cumulative,
                }
            )
    return rows


def build_traffic_profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_campaign: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_campaign[str(row["campaign_id"])].append(row)

    intervals: list[dict[str, Any]] = []
    campaign_rates: dict[str, list[float]] = defaultdict(list)
    campaign_formats: dict[str, str] = {}
    rejected = 0
    for campaign_id, snapshots in by_campaign.items():
        snapshots.sort(key=lambda item: item["observed_utc"])
        campaign_formats[campaign_id] = str(snapshots[0]["ad_format"])
        for before, after in zip(snapshots, snapshots[1:]):
            elapsed_minutes = (
                after["observed_utc"] - before["observed_utc"]
            ).total_seconds() / 60
            increment = (
                int(after["cumulative_impressions"])
                - int(before["cumulative_impressions"])
            )
            if elapsed_minutes <= 0 or elapsed_minutes > 90 or increment < 0:
                rejected += 1
                continue
            rate = increment / elapsed_minutes
            midpoint = before["observed_utc"] + (
                after["observed_utc"] - before["observed_utc"]
            ) / 2
            campaign_rates[campaign_id].append(rate)
            intervals.append(
                {
                    "campaign_id": campaign_id,
                    "ad_format": campaign_formats[campaign_id],
                    "start_utc": before["observed_utc"],
                    "end_utc": after["observed_utc"],
                    "midpoint_utc": midpoint,
                    "elapsed_minutes": elapsed_minutes,
                    "impression_increment": increment,
                    "impressions_per_minute": rate,
                }
            )

    baselines = {
        campaign_id: _median_or_none(rates)
        for campaign_id, rates in campaign_rates.items()
    }
    valid_intervals: list[dict[str, Any]] = []
    for interval in intervals:
        baseline = baselines.get(interval["campaign_id"])
        if baseline is None or baseline <= 0:
            rejected += 1
            continue
        valid_intervals.append(
            {
                **interval,
                "traffic_factor": interval["impressions_per_minute"] / baseline,
            }
        )

    by_hour: dict[int, list[float]] = defaultdict(list)
    by_hour_format: dict[int, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for interval in valid_intervals:
        hour = int(interval["midpoint_utc"].hour)
        factor = float(interval["traffic_factor"])
        by_hour[hour].append(factor)
        by_hour_format[hour][str(interval["ad_format"])].append(factor)

    hourly: list[dict[str, Any]] = []
    for hour in range(24):
        values = by_hour.get(hour, [])
        format_values = by_hour_format.get(hour, {})
        format_medians = {
            ad_format: round(median(factors), 6)
            for ad_format, factors in sorted(format_values.items())
            if factors
        }
        hourly.append(
            {
                "hour_utc": hour,
                "traffic_factor": round(median(values), 6) if values else None,
                "interval_count": len(values),
                "format_medians": format_medians,
            }
        )

    formats = sorted(set(campaign_formats.values()))
    status = "ready"
    warnings: list[str] = []
    if len(valid_intervals) < 48:
        status = "insufficient_baseline"
        warnings.append("fewer than 48 valid traffic-probe intervals")
    if len(formats) < 2:
        warnings.append(
            "only one ad format is present; delivery pacing cannot be cross-checked"
        )
    missing_hours = [row["hour_utc"] for row in hourly if row["traffic_factor"] is None]
    if missing_hours:
        status = "insufficient_baseline"
        warnings.append(f"missing UTC hours: {missing_hours}")

    return {
        "status": status,
        "method": "independent_rr_ad_impression_probe",
        "snapshot_count": len(rows),
        "campaign_count": len(by_campaign),
        "formats": formats,
        "valid_interval_count": len(valid_intervals),
        "rejected_interval_count": rejected,
        "hourly": hourly,
        "warnings": warnings,
        "guardrails": {
            "novel_views_used": False,
            "novel_followers_used": False,
            "absolute_homepage_impressions": False,
            "interpretation": (
                "The profile estimates relative site demand by UTC hour. It does "
                "not reveal the absolute number of visits to Latest Updates or the homepage."
            ),
        },
    }


def _traffic_factor_by_hour(profile: dict[str, Any]) -> dict[int, float]:
    return {
        int(row["hour_utc"]): float(row["traffic_factor"])
        for row in profile.get("hourly", [])
        if row.get("traffic_factor") is not None
    }


def _split_by_hour(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    segments: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(
            hours=1
        )
        boundary = min(next_hour, end)
        segments.append((cursor, boundary))
        cursor = boundary
    return segments


def impression_opportunity(
    exposure_report: dict[str, Any],
    traffic_profile: dict[str, Any],
) -> dict[str, Any]:
    factors = _traffic_factor_by_hour(traffic_profile)
    results: list[dict[str, Any]] = []
    for source_name, surface in exposure_report.get("surfaces", {}).items():
        for episode in surface.get("episodes", []):
            start = _parse_utc(str(episode["first_seen_utc"]))
            exit_upper = episode.get("exit_upper_utc")
            if exit_upper:
                end = _parse_utc(str(exit_upper))
            else:
                end = _parse_utc(str(episode["last_seen_utc"]))
            if end <= start:
                continue
            demand_minutes = 0.0
            missing_minutes = 0.0
            for segment_start, segment_end in _split_by_hour(start, end):
                minutes = (segment_end - segment_start).total_seconds() / 60
                factor = factors.get(segment_start.hour)
                if factor is None:
                    missing_minutes += minutes
                else:
                    demand_minutes += minutes * factor
            rank = episode.get("median_rank")
            top5_units = demand_minutes if rank is not None and float(rank) <= 5 else 0.0
            top10_units = demand_minutes if rank is not None and float(rank) <= 10 else 0.0
            results.append(
                {
                    "source_name": source_name,
                    "fiction_id": episode["fiction_id"],
                    "title": episode["title"],
                    "author": episode.get("author"),
                    "url": episode.get("url"),
                    "first_seen_utc": episode["first_seen_utc"],
                    "exit_upper_utc": exit_upper,
                    "best_rank": episode.get("best_rank"),
                    "median_rank": rank,
                    "residence_estimated_minutes": episode.get(
                        "residence_estimated_minutes"
                    ),
                    "relative_impression_opportunity_units": round(
                        demand_minutes, 6
                    ),
                    "top5_opportunity_units": round(top5_units, 6),
                    "top10_opportunity_units": round(top10_units, 6),
                    "traffic_uncovered_minutes": round(missing_minutes, 6),
                    "absolute_impressions": None,
                }
            )

    hourly_experiments: list[dict[str, Any]] = []
    for row in traffic_profile.get("hourly", []):
        factor = row.get("traffic_factor")
        if factor is None:
            continue
        hourly_experiments.append(
            {
                "hour_utc": int(row["hour_utc"]),
                "traffic_factor": float(factor),
                "five_minute_opportunity": round(5 * float(factor), 6),
                "ten_minute_opportunity": round(10 * float(factor), 6),
                "twenty_minute_opportunity": round(20 * float(factor), 6),
                "minutes_needed_to_match_average_10_minutes": round(
                    10 / float(factor), 6
                )
                if float(factor) > 0
                else None,
            }
        )

    return {
        "status": (
            "ready"
            if traffic_profile.get("status") == "ready"
            else "uncalibrated"
        ),
        "generated_utc": _utc_text(datetime.now(timezone.utc)),
        "methodology": {
            "formula": (
                "relative opportunity = sum(duration_minutes * independent_traffic_factor)"
            ),
            "rank_handling": (
                "No invented scroll probability is applied. Top-5 and top-10 "
                "opportunity are reported as separate observable rank bands."
            ),
            "novel_views_used": False,
            "novel_followers_used": False,
            "absolute_impressions_available": False,
            "causal_claim": False,
        },
        "traffic_profile": traffic_profile,
        "episodes": results,
        "hourly_counterfactuals": hourly_experiments,
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Royal Road potential-impression experiment",
        "",
        f"- Status: `{report['status']}`",
        "- Novel views used in impression model: `false`",
        "- Novel followers used in impression model: `false`",
        "- Absolute impressions available: `false`",
        "",
        "The report measures relative opportunity-to-see, not clicks and not "
        "first-party page impressions.",
        "",
        "## Hourly counterfactuals",
        "",
        "| UTC hour | Traffic factor | 5 min | 10 min | 20 min | Minutes matching average 10 min |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report.get("hourly_counterfactuals", []):
        lines.append(
            "| {hour:02d} | {factor:.3f} | {five:.3f} | {ten:.3f} | "
            "{twenty:.3f} | {match:.3f} |".format(
                hour=row["hour_utc"],
                factor=row["traffic_factor"],
                five=row["five_minute_opportunity"],
                ten=row["ten_minute_opportunity"],
                twenty=row["twenty_minute_opportunity"],
                match=row["minutes_needed_to_match_average_10_minutes"],
            )
        )
    if not report.get("hourly_counterfactuals"):
        lines.append("| — | — | — | — | — | — |")
    lines += [
        "",
        "## Guardrail",
        "",
        "Views and followers may be evaluated later as conversion outcomes, but "
        "they never calibrate impression opportunity.",
        "",
    ]
    return "\n".join(lines)


def write_impression_report(
    exposure_json_path: Path,
    probe_csv_path: Path,
    report_dir: Path,
) -> dict[str, Any]:
    if not exposure_json_path.exists():
        raise FileNotFoundError(exposure_json_path)
    exposure = json.loads(exposure_json_path.read_text(encoding="utf-8"))
    snapshots = load_probe_snapshots(probe_csv_path)
    profile = build_traffic_profile(snapshots)
    report = impression_opportunity(exposure, profile)
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "impression_opportunity_latest.json"
    markdown_path = report_dir / "impression_opportunity_latest.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return {
        "status": report["status"],
        "traffic_profile_status": profile["status"],
        "probe_snapshots": profile["snapshot_count"],
        "valid_probe_intervals": profile["valid_interval_count"],
        "episode_count": len(report["episodes"]),
        "files": {
            "json": str(json_path),
            "markdown": str(markdown_path),
        },
    }
