from datetime import datetime, timezone
from pathlib import Path

from rrlab.impression_model import (
    build_traffic_profile,
    impression_opportunity,
    load_probe_snapshots,
    write_impression_report,
)


def _probe_rows() -> list[dict]:
    rows = []
    for campaign_id, ad_format, scale in (
        ("leaderboard-1", "leaderboard", 10),
        ("rectangle-1", "rectangle", 20),
    ):
        cumulative = 0
        for hour in range(24):
            rate = 2 if hour == 12 else 1
            cumulative += rate * scale
            rows.append(
                {
                    "observed_utc": datetime(
                        2026, 7, 15, hour, 0, tzinfo=timezone.utc
                    ),
                    "campaign_id": campaign_id,
                    "ad_format": ad_format,
                    "cumulative_impressions": cumulative,
                }
            )
    return rows


def _exposure_report(total_views: int) -> dict:
    return {
        "surfaces": {
            "latest_updates_live": {
                "episodes": [
                    {
                        "fiction_id": "100",
                        "title": "Same Exposure",
                        "author": "Author",
                        "url": "https://www.royalroad.com/fiction/100/same-exposure",
                        "first_seen_utc": "2026-07-15T12:00:00Z",
                        "last_seen_utc": "2026-07-15T12:05:00Z",
                        "exit_upper_utc": "2026-07-15T12:05:00Z",
                        "best_rank": 1,
                        "median_rank": 1,
                        "residence_estimated_minutes": 5,
                        "total_views": total_views,
                    }
                ]
            }
        }
    }


def test_probe_profile_uses_ad_impressions_only():
    profile = build_traffic_profile(_probe_rows())

    assert profile["guardrails"]["novel_views_used"] is False
    assert profile["guardrails"]["novel_followers_used"] is False
    assert set(profile["formats"]) == {"leaderboard", "rectangle"}
    noon = next(row for row in profile["hourly"] if row["hour_utc"] == 12)
    assert noon["traffic_factor"] is not None


def test_novel_views_do_not_change_impression_opportunity():
    profile = build_traffic_profile(_probe_rows())

    low_views = impression_opportunity(_exposure_report(10), profile)
    high_views = impression_opportunity(_exposure_report(10_000), profile)

    assert (
        low_views["episodes"][0]["relative_impression_opportunity_units"]
        == high_views["episodes"][0]["relative_impression_opportunity_units"]
    )
    assert low_views["methodology"]["novel_views_used"] is False
    assert low_views["episodes"][0]["absolute_impressions"] is None


def test_missing_probe_data_never_invents_impressions(tmp_path: Path):
    exposure_path = tmp_path / "exposure.json"
    exposure_path.write_text(
        __import__("json").dumps(_exposure_report(100)),
        encoding="utf-8",
    )

    output = write_impression_report(
        exposure_path,
        tmp_path / "missing.csv",
        tmp_path / "reports",
    )

    assert output["status"] == "uncalibrated"
    report = __import__("json").loads(
        Path(output["files"]["json"]).read_text(encoding="utf-8")
    )
    assert report["episodes"][0]["absolute_impressions"] is None


def test_probe_csv_validation(tmp_path: Path):
    path = tmp_path / "probe.csv"
    path.write_text(
        "observed_utc,campaign_id,ad_format,cumulative_impressions\n"
        "2026-07-15T12:00:00Z,c1,leaderboard,100\n",
        encoding="utf-8",
    )

    rows = load_probe_snapshots(path)

    assert rows[0]["campaign_id"] == "c1"
    assert rows[0]["cumulative_impressions"] == 100
