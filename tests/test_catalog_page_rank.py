from datetime import datetime, timezone

from rrlab.catalog import _merge_snapshots
from rrlab.config import CATALOG_BACKFILL_SOURCE
from rrlab.models import FictionObservation, SourceSnapshot


def test_page_local_rank_becomes_absolute_catalog_position():
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    snapshot = SourceSnapshot(
        run_timestamp_utc=now,
        source_name=CATALOG_BACKFILL_SOURCE.name,
        source_family=CATALOG_BACKFILL_SOURCE.family,
        source_url=CATALOG_BACKFILL_SOURCE.url,
        observed_count=1,
        observations=[
            FictionObservation(
                observed_utc=now,
                source_name=CATALOG_BACKFILL_SOURCE.name,
                source_family=CATALOG_BACKFILL_SOURCE.family,
                rank=1,
                fiction_id="700",
                title="Page Three",
                url="https://www.royalroad.com/fiction/700/page-three",
            )
        ],
    )

    merged = _merge_snapshots(
        [(3, snapshot)], CATALOG_BACKFILL_SOURCE, now, complete=False, warnings=[]
    )

    assert merged.observations[0].rank == 41
