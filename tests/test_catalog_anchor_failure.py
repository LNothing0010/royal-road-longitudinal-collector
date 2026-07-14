import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from rrlab.catalog import collect_newest_frontier, persist_frontier_state
from rrlab.config import Settings
from rrlab.storage import Storage

NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def listing_html(fiction_id: int) -> str:
    return f'''<div class="fiction-list-item">
    <h2><a href="/fiction/{fiction_id}/story">Story {fiction_id}</a></h2>
    <span>1 Followers</span><span>2 Views</span><span>3 Pages</span>
    </div>'''


class FakeClient:
    async def get(self, url: str):
        page = int(parse_qs(urlsplit(url).query)["page"][0])
        return SimpleNamespace(
            text=listing_html(1000 + page), status_code=200, elapsed_seconds=0.01
        )


def test_incomplete_frontier_does_not_advance_anchor(tmp_path: Path):
    state_path = tmp_path / "catalog_state.json"
    state_path.write_text(
        json.dumps({"version": 1, "frontier_anchor_ids": ["10"], "backfill_next_page": 2}),
        encoding="utf-8",
    )
    settings = Settings(
        db_path=tmp_path / "rrlab.sqlite",
        raw_dir=tmp_path / "raw",
        report_dir=tmp_path / "reports",
        catalog_state_path=state_path,
        newest_max_pages=2,
        frontier_overlap_pages=1,
        frontier_anchor_limit=100,
    )

    result = asyncio.run(
        collect_newest_frontier(FakeClient(), Storage(settings.db_path, settings.raw_dir), settings, NOW)
    )
    persist_frontier_state(settings, result.summary)

    stored = json.loads(state_path.read_text(encoding="utf-8"))
    assert result.summary["coverage_complete"] is False
    assert stored["frontier_anchor_ids"] == ["10"]
