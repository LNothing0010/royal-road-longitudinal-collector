import json
from datetime import datetime, timezone
from pathlib import Path

from rrlab.impression_model import (
    estimate_page_visits,
    load_external_traffic,
    page_visit_opportunity,
    write_impression_report,
)


def _traffic_rows() -> list[dict]:
    start = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc)
    return [
        {
            "provider": "provider-a",
            "target_url": "royalroad.com/fictions/latest-updates",
            "scope": "page",
            "granularity": "hour",
            "start": start,
            "end": end,
            "visits": 600.0,
        },
        {
            "provider": "provider-b",
            "target_url": "royalroad.com/fictions/latest-updates",
            "scope": "page",
            "granularity": "hour",
            "start": start,
            "end": end,
            "visits": 300.0,
        },
    ]


def _exposure_report(view_delta: int) -> dict:
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
                    }
                ]
            }
        },
        "exposure_traction": [
            {
                "fiction_id": "100",
                "view_delta": view_delta,
            }
        ],
    }


def test_direct_page_hourly_visits_are_prorated_to_residence():
    estimate = estimate_page_visits(
        _traffic_rows(),
        datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 15, 12, 5, tzinfo=timezone.utc),
    )

    assert estimate["provider_estimates"] == {
        "provider-a": 50.0,
        "provider-b": 25.0,
    }
    assert estimate["estimated_page_visits"] == 37.5
    assert estimate["estimate_min"] == 25.0
    assert estimate["estimate_max"] == 50.0


def test_views_are_outcome_not_page_traffic_input():
    low = page_visit_opportunity(_exposure_report(10), _traffic_rows())
    high = page_visit_opportunity(_exposure_report(10_000), _traffic_rows())

    assert (
        low["episodes"][0]["estimated_page_visits"]
        == high["episodes"][0]["estimated_page_visits"]
    )
    assert (
        low["episodes"][0]["views_per_1000_estimated_page_visits"]
        != high["episodes"][0]["views_per_1000_estimated_page_visits"]
    )
    assert low["methodology"]["novel_views_used_to_estimate_page_traffic"] is False
    assert low["methodology"]["novel_views_used_as_outcome"] is True


def test_daily_page_total_without_hourly_shape_is_uncalibrated():
    rows = [
        {
            "provider": "semrush",
            "target_url": "royalroad.com/fictions/latest-updates",
            "scope": "page",
            "granularity": "day",
            "start": datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 7, 16, 0, 0, tzinfo=timezone.utc),
            "visits": 10_000.0,
        }
    ]

    report = page_visit_opportunity(_exposure_report(100), rows)

    assert report["status"] == "uncalibrated"
    assert report["episodes"][0]["estimated_page_visits"] is None


def test_page_baseline_can_be_combined_with_same_provider_domain_hourly():
    day_start = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)
    day_end = datetime(2026, 7, 16, 0, 0, tzinfo=timezone.utc)
    rows = [
        {
            "provider": "semrush",
            "target_url": "royalroad.com/fictions/latest-updates",
            "scope": "page",
            "granularity": "day",
            "start": day_start,
            "end": day_end,
            "visits": 10_000.0,
        },
        {
            "provider": "semrush",
            "target_url": "royalroad.com",
            "scope": "domain",
            "granularity": "day",
            "start": day_start,
            "end": day_end,
            "visits": 1_000_000.0,
        },
        {
            "provider": "semrush",
            "target_url": "royalroad.com",
            "scope": "domain",
            "granularity": "hour",
            "start": datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc),
            "visits": 60_000.0,
        },
    ]

    estimate = estimate_page_visits(
        rows,
        datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 15, 12, 5, tzinfo=timezone.utc),
    )

    assert estimate["estimated_page_visits"] == 50.0
    assert estimate["method"] == "page_baseline_times_domain_hourly_share"


def test_csv_validation_and_missing_data(tmp_path: Path):
    traffic_path = tmp_path / "external.csv"
    traffic_path.write_text(
        "provider,target_url,scope,granularity,period_start_utc,period_end_utc,visits\n"
        "semrush,royalroad.com/fictions/latest-updates,page,month,"
        "2026-06-01T00:00:00Z,2026-07-01T00:00:00Z,100000\n",
        encoding="utf-8",
    )
    rows = load_external_traffic(traffic_path)
    assert rows[0]["provider"] == "semrush"
    assert rows[0]["visits"] == 100000.0

    exposure_path = tmp_path / "exposure.json"
    exposure_path.write_text(json.dumps(_exposure_report(100)), encoding="utf-8")
    output = write_impression_report(
        exposure_path,
        tmp_path / "missing.csv",
        tmp_path / "reports",
    )
    assert output["status"] == "uncalibrated"
    assert Path(output["files"]["json"]).exists()
