import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from rrlab.catalog import _last_page_from_html, _page_url, collect_newest_frontier
from rrlab.config import SOURCE_MAP, Settings
from rrlab.parsers import parse_listing_html
from rrlab.storage import Storage

NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def listing_html(*fiction_ids: int, last_page: int = 100) -> str:
    cards = []
    for fiction_id in fiction_ids:
        cards.append(
            f'''<div class="fiction-list-item">
            <h2><a href="/fiction/{fiction_id}/story-{fiction_id}">Story {fiction_id}</a></h2>
            <a href="/profile/{fiction_id}">Author {fiction_id}</a>
            <span>{fiction_id} Followers</span>
            <span>{fiction_id * 10} Views</span>
            <span>10 Pages</span>
            <span>2 Chapters</span>
            </div>'''
        )
    return (
        "<html><body>"
        + "".join(cards)
        + f'<a href="/fictions/new?page={last_page}">Last</a>'
        + "</body></html>"
    )


class FakeClient:
    def __init__(self, pages: dict[int, str]):
        self.pages = pages
        self.requested_pages: list[int] = []

    async def get(self, url: str):
        page = int(parse_qs(urlsplit(url).query).get("page", ["1"])[0])
        self.requested_pages.append(page)
        return SimpleNamespace(
            text=self.pages[page],
            status_code=200,
            elapsed_seconds=0.01,
        )


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "rrlab.sqlite",
        raw_dir=tmp_path / "raw",
        report_dir=tmp_path / "reports",
        catalog_state_path=tmp_path / "catalog_state.json",
        newest_max_pages=10,
        frontier_overlap_pages=1,
        frontier_anchor_limit=100,
    )


def test_page_url_and_last_page_parser():
    assert _page_url("https://www.royalroad.com/fictions/new?genre=fantasy", 3).endswith(
        "genre=fantasy&page=3"
    )
    assert _last_page_from_html(listing_html(1, last_page=6588)) == 6588


def test_frontier_counts_only_rows_before_prior_anchor_as_new(tmp_path: Path):
    settings = make_settings(tmp_path)
    storage = Storage(settings.db_path, settings.raw_dir)

    baseline = parse_listing_html(
        listing_html(100, 99), SOURCE_MAP["newest"], NOW
    )
    run_id = storage.begin_run(NOW, "test")
    storage.persist_source(run_id, baseline)
    storage.finish_run(run_id, "complete")

    client = FakeClient(
        {
            1: listing_html(102, 100, 97),
            2: listing_html(99, 96),
        }
    )
    result = asyncio.run(collect_newest_frontier(client, storage, settings, NOW))

    assert client.requested_pages == [1, 2]
    assert result.summary["coverage_complete"] is True
    assert result.summary["new_fiction_ids"] == ["102"]
    assert result.summary["overlap_unknown_fictions_excluded"] == 2
    assert [item.fiction_id for item in result.snapshot.observations] == ["102", "100"]


def test_first_run_builds_baseline_without_claiming_new_launches(tmp_path: Path):
    settings = make_settings(tmp_path)
    storage = Storage(settings.db_path, settings.raw_dir)
    client = FakeClient({1: listing_html(200, 199)})

    result = asyncio.run(collect_newest_frontier(client, storage, settings, NOW))

    assert result.summary["coverage_complete"] is False
    assert result.summary["initialize_anchor"] is True
    assert result.summary["new_fictions"] == 0
    assert result.snapshot.observed_count == 2
