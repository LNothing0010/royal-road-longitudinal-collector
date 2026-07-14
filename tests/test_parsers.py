from datetime import datetime, timezone
from pathlib import Path

from rrlab.config import SOURCE_MAP
from rrlab.parsers import parse_detail_html, parse_listing_html

FIXTURES = Path("tests/fixtures")
NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def test_rising_stars_parser():
    html = (FIXTURES / "rising_stars_sample.html").read_text()
    result = parse_listing_html(html, SOURCE_MAP["rs_fantasy"], NOW)
    assert result.observed_count == 2
    assert result.complete is False
    first = result.observations[0]
    assert first.fiction_id == "101"
    assert first.rank == 1
    assert first.followers == 1234
    assert first.total_views == 56789
    assert first.chapter_count == 40
    assert first.word_count_estimate == 210 * 275


def test_latest_updates_releases():
    html = (FIXTURES / "latest_updates_sample.html").read_text()
    result = parse_listing_html(html, SOURCE_MAP["latest_updates"], NOW)
    assert result.observed_count == 1
    assert result.releases[0].chapter_id == "9001"
    assert result.releases[0].published_utc is not None


def test_detail_parser():
    html = (FIXTURES / "detail_sample.html").read_text()
    detail = parse_detail_html(html, "https://www.royalroad.com/fiction/101/alpha", NOW)
    obs = detail.observation
    assert obs.fiction_id == "101"
    assert obs.title == "Alpha"
    assert obs.followers == 1300
    assert obs.total_views == 60000
    assert obs.favorites == 200
    assert obs.rating_count == 42
    assert len(detail.releases) == 2
    assert obs.first_chapter_utc is not None
    assert "patreon.com" in obs.marketing_urls[0]
