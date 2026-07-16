from datetime import datetime, timedelta, timezone
from pathlib import Path

from rrlab.exposure import (
    HOME_LATEST,
    HOME_RS,
    NEWEST_LIVE,
    build_exposure_analysis,
    parse_homepage_exposure,
    write_exposure_analysis,
)
from rrlab.models import FictionObservation, SourceSnapshot
from rrlab.storage import Storage


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


def _observation(source: str, fiction_id: str, rank: int, observed: datetime) -> FictionObservation:
    return FictionObservation(
        observed_utc=observed,
        source_name=source,
        source_family="organic_exposure",
        rank=rank,
        fiction_id=fiction_id,
        title=f"Story {fiction_id}",
        author=f"Author {fiction_id}",
        url=f"https://www.royalroad.com/fiction/{fiction_id}/story-{fiction_id}",
    )


def _persist_sample(storage: Storage, timestamp: datetime, memberships: dict[str, list[str]]) -> int:
    run_id = storage.begin_run(timestamp, "test")
    for source_name, fiction_ids in memberships.items():
        snapshot = SourceSnapshot(
            run_timestamp_utc=timestamp,
            source_name=source_name,
            source_family="organic_exposure",
            source_url="https://www.royalroad.com/home",
            observed_count=len(fiction_ids),
            observations=[
                _observation(source_name, fiction_id, index + 1, timestamp)
                for index, fiction_id in enumerate(fiction_ids)
            ],
        )
        storage.persist_source(run_id, snapshot)
    storage.finish_run(run_id, "complete")
    return run_id


def test_homepage_sections_are_parsed_separately():
    html = """
    <html><body>
      <h2>Latest Updates</h2>
      <article class="fiction-card">
        <h3><a href="/fiction/101/alpha">Alpha</a></h3>
        <a href="/profile/1">Alice</a>
        <a href="/fiction/101/alpha/chapter/9001/chapter-one">Chapter One</a>
        <time unixtime="1784203200">just now</time>
      </article>
      <article class="fiction-card">
        <h3><a href="/fiction/102/beta">Beta</a></h3>
      </article>
      <h2>Rising Stars</h2>
      <article class="fiction-card">
        <h3><a href="/fiction/201/gamma">Gamma</a></h3>
        <span>44 Followers</span>
      </article>
      <h2>Popular This Week</h2>
      <a href="/fiction/999/not-in-section">Not in section</a>
    </body></html>
    """

    latest, rising = parse_homepage_exposure(html, NOW)

    assert latest.source_name == HOME_LATEST
    assert [item.fiction_id for item in latest.observations] == ["101", "102"]
    assert [item.rank for item in latest.observations] == [1, 2]
    assert latest.observations[0].author == "Alice"
    assert latest.releases[0].chapter_id == "9001"
    assert latest.releases[0].published_utc is not None

    assert rising.source_name == HOME_RS
    assert [item.fiction_id for item in rising.observations] == ["201"]
    assert rising.observations[0].followers == 44


def test_residence_is_interval_censored_between_samples(tmp_path: Path):
    storage = Storage(tmp_path / "rrlab.sqlite", tmp_path / "raw")
    _persist_sample(
        storage,
        NOW,
        {HOME_LATEST: ["101"], HOME_RS: [], NEWEST_LIVE: []},
    )
    _persist_sample(
        storage,
        NOW + timedelta(minutes=15),
        {HOME_LATEST: ["102", "101"], HOME_RS: [], NEWEST_LIVE: ["102"]},
    )
    _persist_sample(
        storage,
        NOW + timedelta(minutes=30),
        {HOME_LATEST: ["102"], HOME_RS: [], NEWEST_LIVE: ["102"]},
    )

    report = build_exposure_analysis(storage.db_path, lookback_hours=24)
    episodes = report["surfaces"][HOME_LATEST]["episodes"]
    alpha = next(item for item in episodes if item["fiction_id"] == "101")

    assert alpha["residence_lower_minutes"] == 15.0
    assert alpha["residence_upper_minutes"] == 30.0
    assert alpha["residence_estimated_minutes"] == 22.5
    assert alpha["best_rank"] == 1

    beta_window = next(
        item for item in report["publication_windows"] if item["fiction_id"] == "102"
    )
    assert beta_window["precision"] == "interval_censored"
    assert beta_window["publication_lower_bound_utc"] == "2026-07-16T12:00:00Z"
    assert beta_window["publication_upper_bound_utc"] == "2026-07-16T12:15:00Z"
    assert beta_window["maximum_age_at_first_seen_minutes"] == 15.0


def test_exposure_analysis_writes_json_markdown_and_csv(tmp_path: Path):
    storage = Storage(tmp_path / "rrlab.sqlite", tmp_path / "raw")
    _persist_sample(
        storage,
        NOW,
        {HOME_LATEST: ["101"], HOME_RS: ["201"], NEWEST_LIVE: ["101"]},
    )
    report_dir = tmp_path / "reports"

    output = write_exposure_analysis(storage.db_path, report_dir, lookback_hours=24)

    assert Path(output["files"]["json"]).exists()
    assert Path(output["files"]["markdown"]).exists()
    assert Path(output["files"]["csv"]).exists()
    markdown = Path(output["files"]["markdown"]).read_text(encoding="utf-8")
    assert "Direct homepage traffic is not public" in markdown
    assert "Homepage — Latest Updates" in markdown
