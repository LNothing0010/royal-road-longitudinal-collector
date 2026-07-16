from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from . import __version__
from .config import EXPOSURE_SOURCES, HOME_URL, Settings, SourceSpec
from .http_source import PublicHtmlClient
from .models import FictionObservation, ReleaseObservation, SourceSnapshot
from .parsers import (
    BASE,
    CHAPTER_RE,
    FICTION_RE,
    metric_from_text,
    parse_datetime_text,
    parse_listing_html,
)
from .storage import Storage

HOME_LATEST = "home_latest_updates"
HOME_RS = "home_rising_stars"
LATEST_LIVE = "latest_updates_live"
NEWEST_LIVE = "newest_live"
SURFACES = (HOME_LATEST, HOME_RS, LATEST_LIVE, NEWEST_LIVE)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalized(text: str) -> str:
    return " ".join(text.split()).strip().casefold()


def _find_heading(soup: BeautifulSoup, label: str) -> Tag | None:
    target = _normalized(label)
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5"]):
        if _normalized(tag.get_text(" ", strip=True)) == target:
            return tag
    return None


def _card_for_anchor(anchor: Tag) -> Tag:
    node: Tag | None = anchor
    best = anchor
    for _ in range(8):
        if not isinstance(node, Tag) or not isinstance(node.parent, Tag):
            break
        node = node.parent
        fiction_ids = {
            match.group(1)
            for link in node.select('a[href*="/fiction/"]')
            if (match := FICTION_RE.search(str(link.get("href", ""))))
        }
        if len(fiction_ids) == 1:
            best = node
            classes = " ".join(node.get("class", [])).casefold()
            if (
                node.name in {"li", "article"}
                or "fiction" in classes
                or node.select_one('a[href*="/chapter/"]') is not None
            ):
                break
        elif len(fiction_ids) > 1:
            break
    return best


def _release_links(card: Tag, fiction_id: str, source_name: str, observed: datetime) -> list[ReleaseObservation]:
    releases: list[ReleaseObservation] = []
    seen: set[str] = set()
    for anchor in card.select('a[href*="/chapter/"]'):
        href = str(anchor.get("href", ""))
        chapter_match = CHAPTER_RE.search(href)
        chapter_id = chapter_match.group(1) if chapter_match else None
        key = chapter_id or href
        if not key or key in seen:
            continue
        seen.add(key)
        container = anchor.find_parent(["li", "tr"]) or anchor.parent
        time_el = container.select_one("time") if isinstance(container, Tag) else None
        published = None
        precision = "unknown"
        if time_el:
            unix_value = time_el.get("unixtime") or time_el.get("data-timestamp")
            if unix_value and str(unix_value).isdigit():
                published = datetime.fromtimestamp(int(unix_value), tz=timezone.utc)
                precision = "unix"
            else:
                time_text = (
                    time_el.get("title")
                    or time_el.get("datetime")
                    or time_el.get_text(" ", strip=True)
                )
                published, precision = parse_datetime_text(str(time_text), observed)
        if published is None and isinstance(container, Tag):
            nearby = " ".join(container.get_text(" ", strip=True).split())
            title = " ".join(anchor.get_text(" ", strip=True).split())
            published, precision = parse_datetime_text(nearby.replace(title, "", 1), observed)
        releases.append(
            ReleaseObservation(
                fiction_id=fiction_id,
                chapter_id=chapter_id,
                chapter_title=" ".join(anchor.get_text(" ", strip=True).split()),
                chapter_url=urljoin(BASE, href),
                published_utc=published,
                observed_utc=observed,
                source_name=source_name,
                date_precision=precision,
            )
        )
    return releases


def parse_home_section(
    html: str,
    observed: datetime,
    *,
    heading_label: str,
    stop_label: str,
    source_name: str,
) -> SourceSnapshot:
    soup = BeautifulSoup(html, "lxml")
    heading = _find_heading(soup, heading_label)
    warnings: list[str] = []
    observations: list[FictionObservation] = []
    releases: list[ReleaseObservation] = []
    seen: set[str] = set()

    if heading is None:
        warnings.append(f"homepage heading not found: {heading_label}")
    else:
        stop = _normalized(stop_label)
        for element in heading.find_all_next(["h1", "h2", "h3", "h4", "h5", "a"]):
            if element is heading:
                continue
            if element.name in {"h1", "h2", "h3", "h4", "h5"}:
                if _normalized(element.get_text(" ", strip=True)) == stop:
                    break
                continue
            href = str(element.get("href", ""))
            match = FICTION_RE.search(href)
            if not match or match.group(1) in seen:
                continue
            title = " ".join(element.get_text(" ", strip=True).split())
            if not title:
                continue
            fiction_id = match.group(1)
            seen.add(fiction_id)
            card = _card_for_anchor(element)
            text = " ".join(card.get_text(" ", strip=True).split())
            author_el = card.select_one('a[href*="/profile/"]')
            observations.append(
                FictionObservation(
                    observed_utc=observed,
                    source_name=source_name,
                    source_family="organic_exposure",
                    rank=len(observations) + 1,
                    fiction_id=fiction_id,
                    title=title,
                    url=urljoin(BASE, href),
                    author=author_el.get_text(" ", strip=True) if author_el else None,
                    followers=metric_from_text(text, ("followers", "follower")),
                )
            )
            releases.extend(_release_links(card, fiction_id, source_name, observed))

    if not observations:
        warnings.append(f"no homepage fiction parsed for {source_name}")
    return SourceSnapshot(
        run_timestamp_utc=observed,
        source_name=source_name,
        source_family="organic_exposure",
        source_url=HOME_URL,
        expected_count=None,
        observed_count=len(observations),
        complete=None,
        observations=observations,
        releases=releases,
        warnings=warnings,
    )


def parse_homepage_exposure(html: str, observed: datetime) -> tuple[SourceSnapshot, SourceSnapshot]:
    return (
        parse_home_section(
            html,
            observed,
            heading_label="Latest Updates",
            stop_label="Rising Stars",
            source_name=HOME_LATEST,
        ),
        parse_home_section(
            html,
            observed,
            heading_label="Rising Stars",
            stop_label="Popular This Week",
            source_name=HOME_RS,
        ),
    )


async def collect_exposure(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or Settings()
    timestamp = datetime.now(timezone.utc).replace(microsecond=0)
    storage = Storage(settings.db_path, settings.raw_dir)
    run_id = storage.begin_run(timestamp, __version__)
    client = PublicHtmlClient(settings)
    snapshots: list[SourceSnapshot] = []
    errors: list[str] = []
    try:
        try:
            home = await client.get(HOME_URL)
            home_latest, home_rs = parse_homepage_exposure(home.text, timestamp)
            home_latest = home_latest.model_copy(
                update={"http_status": home.status_code, "fetch_seconds": home.elapsed_seconds}
            )
            home_rs = home_rs.model_copy(
                update={"http_status": home.status_code, "fetch_seconds": home.elapsed_seconds}
            )
            snapshots.extend((home_latest, home_rs))
        except Exception as exc:
            errors.append(f"home: {type(exc).__name__}: {exc}")

        for spec in EXPOSURE_SOURCES:
            try:
                fetched = await client.get(spec.url)
                snapshot = parse_listing_html(
                    fetched.text,
                    spec,
                    timestamp,
                    http_status=fetched.status_code,
                    fetch_seconds=fetched.elapsed_seconds,
                )
                snapshots.append(snapshot)
            except Exception as exc:
                errors.append(f"{spec.name}: {type(exc).__name__}: {exc}")

        observed_names = {snapshot.source_name for snapshot in snapshots}
        for source_name in SURFACES:
            if source_name in observed_names:
                continue
            source_url = HOME_URL
            for spec in EXPOSURE_SOURCES:
                if spec.name == source_name:
                    source_url = spec.url
                    break
            snapshots.append(
                SourceSnapshot(
                    run_timestamp_utc=timestamp,
                    source_name=source_name,
                    source_family="organic_exposure",
                    source_url=source_url,
                    observed_count=0,
                    complete=False,
                    warnings=["source unavailable in exposure run"],
                )
            )

        for snapshot in snapshots:
            storage.persist_source(
                run_id,
                snapshot,
                None,
            )

        required = {HOME_LATEST, LATEST_LIVE, NEWEST_LIVE}
        usable = {
            snapshot.source_name
            for snapshot in snapshots
            if snapshot.observed_count > 0
        }
        status = "complete" if not errors and required.issubset(usable) else "partial"
        storage.finish_run(run_id, status, "\n".join(errors) or None)
        report = write_exposure_analysis(
            settings.db_path,
            settings.report_dir,
            lookback_hours=168,
        )
        return {
            "run_id": run_id,
            "timestamp_utc": _utc_text(timestamp),
            "status": status,
            "sources": [
                {
                    "name": snapshot.source_name,
                    "count": snapshot.observed_count,
                    "warnings": snapshot.warnings,
                }
                for snapshot in snapshots
            ],
            "errors": errors,
            "analysis_summary": report["summary"],
            "analysis_files": report["files"],
        }
    except Exception as exc:
        storage.finish_run(run_id, "failed", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        await client.close()


def run_exposure_collection(settings: Settings | None = None) -> dict[str, Any]:
    return asyncio.run(collect_exposure(settings))


def _load_samples(
    conn: sqlite3.Connection,
    source_name: str,
    start_utc: str,
    end_utc: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.run_id,r.timestamp_utc,lm.fiction_id,lm.rank,f.title,f.author,f.url
        FROM source_snapshot ss
        JOIN run r USING(run_id)
        LEFT JOIN listing_membership lm
          ON lm.run_id=ss.run_id AND lm.source_name=ss.source_name
        LEFT JOIN fiction f ON f.fiction_id=lm.fiction_id
        WHERE ss.source_name=?
          AND r.timestamp_utc>=?
          AND r.timestamp_utc<=?
        ORDER BY r.timestamp_utc,lm.rank
        """,
        (source_name, start_utc, end_utc),
    ).fetchall()
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        run_id = int(row["run_id"])
        sample = grouped.setdefault(
            run_id,
            {
                "run_id": run_id,
                "timestamp": _parse_utc(str(row["timestamp_utc"])),
                "members": {},
            },
        )
        if row["fiction_id"] is not None:
            sample["members"][str(row["fiction_id"])] = {
                "rank": int(row["rank"]) if row["rank"] is not None else None,
                "title": row["title"],
                "author": row["author"],
                "url": row["url"],
            }
    return list(grouped.values())


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _surface_analysis(source_name: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {
            "source_name": source_name,
            "sample_count": 0,
            "unique_fictions": 0,
            "episodes": [],
            "hourly": [],
        }
    sample_deltas = [
        (samples[index]["timestamp"] - samples[index - 1]["timestamp"]).total_seconds() / 60
        for index in range(1, len(samples))
        if samples[index]["timestamp"] > samples[index - 1]["timestamp"]
    ]
    cadence = median(sample_deltas) if sample_deltas else 15.0
    active: dict[str, dict[str, Any]] = {}
    episodes: list[dict[str, Any]] = []
    prior_ids: set[str] = set()
    prior_timestamp: datetime | None = None
    hourly_turnover: dict[int, list[int]] = defaultdict(list)

    def close_episode(fiction_id: str, exit_upper: datetime | None) -> None:
        episode = active.pop(fiction_id)
        first_seen = episode["first_seen"]
        last_seen = episode["last_seen"]
        lower = max(0.0, (last_seen - first_seen).total_seconds() / 60)
        upper = (
            max(lower, (exit_upper - first_seen).total_seconds() / 60)
            if exit_upper is not None
            else None
        )
        estimated = (lower + upper) / 2 if upper is not None else lower + cadence / 2
        weighted = sum(
            duration / math.log2(rank + 1)
            for duration, rank in episode["weighted_intervals"]
            if rank is not None and rank >= 1
        )
        episodes.append(
            {
                "source_name": source_name,
                "fiction_id": fiction_id,
                "title": episode["title"],
                "author": episode["author"],
                "url": episode["url"],
                "entry_lower_utc": _utc_text(episode["entry_lower"]) if episode["entry_lower"] else None,
                "first_seen_utc": _utc_text(first_seen),
                "last_seen_utc": _utc_text(last_seen),
                "exit_upper_utc": _utc_text(exit_upper) if exit_upper else None,
                "active_at_window_end": exit_upper is None,
                "sample_count": episode["sample_count"],
                "best_rank": min(episode["ranks"]) if episode["ranks"] else None,
                "median_rank": median(episode["ranks"]) if episode["ranks"] else None,
                "residence_lower_minutes": round(lower, 3),
                "residence_upper_minutes": round(upper, 3) if upper is not None else None,
                "residence_estimated_minutes": round(estimated, 3),
                "position_weighted_minutes": round(weighted, 3),
                "entry_hour_utc": first_seen.hour,
            }
        )

    for index, sample in enumerate(samples):
        timestamp = sample["timestamp"]
        members = sample["members"]
        current_ids = set(members)
        hourly_turnover[timestamp.hour].append(len(current_ids - prior_ids))
        for fiction_id in list(active):
            if fiction_id not in current_ids:
                close_episode(fiction_id, timestamp)
        next_timestamp = samples[index + 1]["timestamp"] if index + 1 < len(samples) else None
        interval = cadence
        if next_timestamp is not None:
            interval = min(cadence * 2, max(0.0, (next_timestamp - timestamp).total_seconds() / 60))
        for fiction_id, member in members.items():
            if fiction_id not in active:
                active[fiction_id] = {
                    "title": member["title"],
                    "author": member["author"],
                    "url": member["url"],
                    "entry_lower": prior_timestamp if fiction_id not in prior_ids else None,
                    "first_seen": timestamp,
                    "last_seen": timestamp,
                    "sample_count": 0,
                    "ranks": [],
                    "weighted_intervals": [],
                }
            episode = active[fiction_id]
            episode["last_seen"] = timestamp
            episode["sample_count"] += 1
            if member["rank"] is not None:
                episode["ranks"].append(member["rank"])
                episode["weighted_intervals"].append((interval, member["rank"]))
        prior_ids = current_ids
        prior_timestamp = timestamp

    for fiction_id in list(active):
        close_episode(fiction_id, None)

    completed = [episode for episode in episodes if not episode["active_at_window_end"]]
    residence = [float(episode["residence_estimated_minutes"]) for episode in completed]
    by_hour: dict[int, list[float]] = defaultdict(list)
    for episode in completed:
        by_hour[int(episode["entry_hour_utc"])].append(
            float(episode["residence_estimated_minutes"])
        )
    hourly = []
    for hour in range(24):
        values = by_hour.get(hour, [])
        turnover_values = hourly_turnover.get(hour, [])
        hourly.append(
            {
                "hour_utc": hour,
                "completed_episodes": len(values),
                "median_residence_minutes": round(median(values), 3) if values else None,
                "median_new_entries_per_sample": (
                    round(median(turnover_values), 3) if turnover_values else None
                ),
                "max_new_entries_per_sample": max(turnover_values) if turnover_values else None,
            }
        )
    return {
        "source_name": source_name,
        "sample_count": len(samples),
        "first_sample_utc": _utc_text(samples[0]["timestamp"]),
        "last_sample_utc": _utc_text(samples[-1]["timestamp"]),
        "median_cadence_minutes": round(cadence, 3),
        "unique_fictions": len({fid for sample in samples for fid in sample["members"]}),
        "episode_count": len(episodes),
        "completed_episode_count": len(completed),
        "median_residence_minutes": round(median(residence), 3) if residence else None,
        "p25_residence_minutes": round(_percentile(residence, 0.25), 3) if residence else None,
        "p75_residence_minutes": round(_percentile(residence, 0.75), 3) if residence else None,
        "episodes": sorted(episodes, key=lambda item: item["first_seen_utc"], reverse=True),
        "hourly": hourly,
    }


def _publication_windows(
    conn: sqlite3.Connection,
    newest_analysis: dict[str, Any],
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for episode in newest_analysis.get("episodes", []):
        row = conn.execute(
            """
            SELECT MIN(published_utc) AS published_utc
            FROM release_event
            WHERE fiction_id=? AND published_utc IS NOT NULL
            """,
            (episode["fiction_id"],),
        ).fetchone()
        exact = row["published_utc"] if row and row["published_utc"] else None
        first_seen = _parse_utc(episode["first_seen_utc"])
        lower = episode.get("entry_lower_utc")
        max_age = None
        if lower:
            max_age = (first_seen - _parse_utc(lower)).total_seconds() / 60
        age_at_seen = None
        if exact:
            age_at_seen = max(0.0, (first_seen - _parse_utc(str(exact))).total_seconds() / 60)
        windows.append(
            {
                "fiction_id": episode["fiction_id"],
                "title": episode["title"],
                "author": episode["author"],
                "url": episode["url"],
                "publication_time_utc": exact,
                "publication_lower_bound_utc": None if exact else lower,
                "publication_upper_bound_utc": None if exact else episode["first_seen_utc"],
                "first_seen_utc": episode["first_seen_utc"],
                "age_at_first_seen_minutes": round(age_at_seen, 3) if age_at_seen is not None else None,
                "maximum_age_at_first_seen_minutes": round(max_age, 3) if max_age is not None else None,
                "precision": "exact_public_timestamp" if exact else "interval_censored",
            }
        )
    return windows


def _traction_linkage(
    conn: sqlite3.Connection,
    analyses: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    aggregate: dict[str, dict[str, Any]] = {}
    for source_name in (HOME_LATEST, LATEST_LIVE, NEWEST_LIVE):
        for episode in analyses[source_name].get("episodes", []):
            row = aggregate.setdefault(
                episode["fiction_id"],
                {
                    "fiction_id": episode["fiction_id"],
                    "title": episode["title"],
                    "author": episode["author"],
                    "url": episode["url"],
                    "first_exposure": episode["first_seen_utc"],
                    "last_exposure": episode["last_seen_utc"],
                    "latest_updates_minutes": 0.0,
                    "home_minutes": 0.0,
                    "home_weighted_minutes": 0.0,
                    "newest_minutes": 0.0,
                },
            )
            row["first_exposure"] = min(row["first_exposure"], episode["first_seen_utc"])
            row["last_exposure"] = max(row["last_exposure"], episode["last_seen_utc"])
            minutes = float(episode["residence_estimated_minutes"])
            if source_name == HOME_LATEST:
                row["home_minutes"] += minutes
                row["home_weighted_minutes"] += float(episode["position_weighted_minutes"])
            elif source_name == LATEST_LIVE:
                row["latest_updates_minutes"] += minutes
            elif source_name == NEWEST_LIVE:
                row["newest_minutes"] += minutes

    output: list[dict[str, Any]] = []
    for row in aggregate.values():
        first = _parse_utc(row["first_exposure"]) - timedelta(hours=2)
        last = _parse_utc(row["last_exposure"]) + timedelta(hours=6)
        metrics = conn.execute(
            """
            SELECT r.timestamp_utc,mo.followers,mo.total_views
            FROM metric_observation mo
            JOIN run r USING(run_id)
            WHERE mo.fiction_id=?
              AND mo.source_name='fiction_detail'
              AND r.timestamp_utc>=?
              AND r.timestamp_utc<=?
              AND mo.followers IS NOT NULL
              AND mo.total_views IS NOT NULL
            ORDER BY r.timestamp_utc
            """,
            (row["fiction_id"], _utc_text(first), _utc_text(last)),
        ).fetchall()
        follower_delta = None
        view_delta = None
        metric_elapsed = None
        if len(metrics) >= 2:
            follower_delta = int(metrics[-1]["followers"]) - int(metrics[0]["followers"])
            view_delta = int(metrics[-1]["total_views"]) - int(metrics[0]["total_views"])
            metric_elapsed = (
                _parse_utc(str(metrics[-1]["timestamp_utc"]))
                - _parse_utc(str(metrics[0]["timestamp_utc"]))
            ).total_seconds() / 3600
        output.append(
            {
                **row,
                "latest_updates_minutes": round(row["latest_updates_minutes"], 3),
                "home_minutes": round(row["home_minutes"], 3),
                "home_weighted_minutes": round(row["home_weighted_minutes"], 3),
                "newest_minutes": round(row["newest_minutes"], 3),
                "appeared_on_home": row["home_minutes"] > 0,
                "metric_observation_count": len(metrics),
                "metric_elapsed_hours": round(metric_elapsed, 3) if metric_elapsed is not None else None,
                "follower_delta": follower_delta,
                "view_delta": view_delta,
            }
        )
    return sorted(output, key=lambda item: item["home_weighted_minutes"], reverse=True)


def _pearson(rows: list[dict[str, Any]], x_key: str, y_key: str) -> dict[str, Any]:
    pairs = [
        (float(row[x_key]), float(row[y_key]))
        for row in rows
        if row.get(x_key) is not None and row.get(y_key) is not None
    ]
    if len(pairs) < 5:
        return {"n": len(pairs), "correlation": None, "status": "insufficient_sample"}
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    denominator = math.sqrt(
        sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)
    )
    correlation = None if denominator == 0 else numerator / denominator
    return {
        "n": len(pairs),
        "correlation": round(correlation, 4) if correlation is not None else None,
        "status": "descriptive_only",
    }


def build_exposure_analysis(
    db_path: Path,
    *,
    lookback_hours: int = 168,
) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        latest_row = conn.execute(
            """
            SELECT MAX(r.timestamp_utc)
            FROM run r
            JOIN source_snapshot ss USING(run_id)
            WHERE ss.source_family='organic_exposure'
            """
        ).fetchone()
        if latest_row is None or latest_row[0] is None:
            end = datetime.now(timezone.utc)
        else:
            end = _parse_utc(str(latest_row[0]))
        start = end - timedelta(hours=lookback_hours)
        analyses: dict[str, dict[str, Any]] = {}
        for source_name in SURFACES:
            analyses[source_name] = _surface_analysis(
                source_name,
                _load_samples(conn, source_name, _utc_text(start), _utc_text(end)),
            )
        publications = _publication_windows(conn, analyses[NEWEST_LIVE])
        traction = _traction_linkage(conn, analyses)
        correlations = {
            "home_weighted_exposure_vs_view_delta": _pearson(
                traction, "home_weighted_minutes", "view_delta"
            ),
            "latest_updates_exposure_vs_view_delta": _pearson(
                traction, "latest_updates_minutes", "view_delta"
            ),
            "home_weighted_exposure_vs_follower_delta": _pearson(
                traction, "home_weighted_minutes", "follower_delta"
            ),
        }
        sample_count = analyses[LATEST_LIVE]["sample_count"]
        elapsed_hours = max(0.0, (end - start).total_seconds() / 3600)
        readiness = (
            "directional"
            if sample_count >= 48
            else "collecting_baseline"
        )
        return {
            "generated_utc": _utc_text(datetime.now(timezone.utc)),
            "window_start_utc": _utc_text(start),
            "window_end_utc": _utc_text(end),
            "lookback_hours": lookback_hours,
            "methodology": {
                "traffic_measurement": "Royal Road does not expose homepage visit counts; section residence and position are used as organic exposure proxies.",
                "residence": "Interval-censored between consecutive samples; reported as lower, upper and midpoint estimates.",
                "position_weight": "minutes / log2(rank + 1)",
                "causality": "Correlations are descriptive and do not establish that exposure caused traction.",
            },
            "summary": {
                "status": readiness,
                "latest_updates_samples": sample_count,
                "observed_window_hours": round(elapsed_hours, 3),
                "home_latest_median_residence_minutes": analyses[HOME_LATEST].get("median_residence_minutes"),
                "latest_updates_median_residence_minutes": analyses[LATEST_LIVE].get("median_residence_minutes"),
                "newest_median_residence_minutes": analyses[NEWEST_LIVE].get("median_residence_minutes"),
                "publication_windows": len(publications),
                "traction_linkages": sum(1 for row in traction if row["view_delta"] is not None),
            },
            "surfaces": analyses,
            "publication_windows": publications,
            "exposure_traction": traction,
            "correlations": correlations,
        }
    finally:
        conn.close()


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Royal Road organic exposure",
        "",
        f"- Window: `{report['window_start_utc']}` to `{report['window_end_utc']}`",
        f"- Status: `{summary['status']}`",
        f"- Latest Updates samples: `{summary['latest_updates_samples']}`",
        "- Direct homepage traffic is not public; this report measures section residence and position-weighted visibility.",
        "",
        "## Surface residence",
        "",
        "| Surface | Samples | Unique fiction | Median residence | P25 | P75 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    labels = {
        HOME_LATEST: "Homepage — Latest Updates",
        HOME_RS: "Homepage — Rising Stars",
        LATEST_LIVE: "Latest Updates page 1",
        NEWEST_LIVE: "Newest Fictions page 1",
    }
    for source_name in SURFACES:
        surface = report["surfaces"][source_name]
        lines.append(
            "| {label} | {samples} | {unique} | {median} | {p25} | {p75} |".format(
                label=labels[source_name],
                samples=surface.get("sample_count", 0),
                unique=surface.get("unique_fictions", 0),
                median=surface.get("median_residence_minutes") or "—",
                p25=surface.get("p25_residence_minutes") or "—",
                p75=surface.get("p75_residence_minutes") or "—",
            )
        )
    lines += ["", "## Best and most crowded UTC hours", ""]
    home_hours = [
        row
        for row in report["surfaces"][HOME_LATEST].get("hourly", [])
        if row["median_residence_minutes"] is not None
    ]
    for row in sorted(home_hours, key=lambda item: item["median_residence_minutes"], reverse=True)[:5]:
        lines.append(
            f"- `{row['hour_utc']:02d}:00–{row['hour_utc']:02d}:59 UTC`: median homepage residence "
            f"`{row['median_residence_minutes']}` minutes; median new entries/sample "
            f"`{row['median_new_entries_per_sample']}`."
        )
    if not home_hours:
        lines.append("- Baseline still too short for hour-of-day comparisons.")
    lines += ["", "## Recent publication windows", ""]
    for item in report["publication_windows"][:20]:
        if item["publication_time_utc"]:
            timing = f"published `{item['publication_time_utc']}`"
        else:
            timing = (
                f"published between `{item['publication_lower_bound_utc']}` and "
                f"`{item['publication_upper_bound_utc']}`"
            )
        lines.append(f"- **{item['title']}** — {timing} ({item['precision']}).")
    if not report["publication_windows"]:
        lines.append("- No Newest live sample is available yet.")
    lines += ["", "## Exposure-to-traction checks", ""]
    for name, result in report["correlations"].items():
        lines.append(
            f"- `{name}`: n=`{result['n']}`, correlation=`{result['correlation']}`, status=`{result['status']}`."
        )
    lines += [
        "",
        "> Correlation is descriptive. Publication quality, existing audience, genre, chapter count, ads, shout-outs and time since launch remain confounders.",
        "",
    ]
    return "\n".join(lines)


def _episodes_csv(report: dict[str, Any]) -> str:
    fields = [
        "source_name",
        "fiction_id",
        "title",
        "author",
        "url",
        "entry_lower_utc",
        "first_seen_utc",
        "last_seen_utc",
        "exit_upper_utc",
        "active_at_window_end",
        "sample_count",
        "best_rank",
        "median_rank",
        "residence_lower_minutes",
        "residence_upper_minutes",
        "residence_estimated_minutes",
        "position_weighted_minutes",
        "entry_hour_utc",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for source_name in SURFACES:
        for episode in report["surfaces"][source_name].get("episodes", []):
            writer.writerow({field: episode.get(field) for field in fields})
    return buffer.getvalue()


def write_exposure_analysis(
    db_path: Path,
    report_dir: Path,
    *,
    lookback_hours: int = 168,
) -> dict[str, Any]:
    report = build_exposure_analysis(db_path, lookback_hours=lookback_hours)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = report["window_end_utc"].replace(":", "").replace("-", "")
    json_text = json.dumps(report, indent=2, ensure_ascii=False)
    markdown_text = _markdown(report)
    csv_text = _episodes_csv(report)
    files = {
        "json": str(report_dir / "exposure_analysis_latest.json"),
        "markdown": str(report_dir / "exposure_analysis_latest.md"),
        "csv": str(report_dir / "exposure_analysis_latest.csv"),
    }
    Path(files["json"]).write_text(json_text + "\n", encoding="utf-8")
    Path(files["markdown"]).write_text(markdown_text, encoding="utf-8")
    Path(files["csv"]).write_text(csv_text, encoding="utf-8")
    (report_dir / f"exposure_analysis_{stamp}.json").write_text(json_text + "\n", encoding="utf-8")
    (report_dir / f"exposure_analysis_{stamp}.md").write_text(markdown_text, encoding="utf-8")
    (report_dir / f"exposure_analysis_{stamp}.csv").write_text(csv_text, encoding="utf-8")
    return {"summary": report["summary"], "files": files}
