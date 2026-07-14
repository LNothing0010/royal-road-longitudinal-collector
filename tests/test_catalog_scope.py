from rrlab.config import CATALOG_BACKFILL_SOURCE


def test_catalog_backfill_has_dedicated_source_family():
    assert CATALOG_BACKFILL_SOURCE.family == "catalog"
    assert CATALOG_BACKFILL_SOURCE.is_rs is False
