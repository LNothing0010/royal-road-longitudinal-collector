from rrlab.config import CATALOG_BACKFILL_SOURCE, SOURCE_MAP, SOURCES


def test_catalog_backfill_is_addressable_but_not_in_hourly_source_loop():
    assert SOURCE_MAP["catalog_backfill"] == CATALOG_BACKFILL_SOURCE
    assert all(source.name != "catalog_backfill" for source in SOURCES)
