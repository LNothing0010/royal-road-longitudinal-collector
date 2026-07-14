from rrlab.config import Settings


def test_catalog_defaults_are_bounded_and_rate_limited():
    settings = Settings()

    assert settings.min_delay_seconds >= 1.0
    assert settings.newest_max_pages == 25
    assert settings.backfill_pages_per_run == 75
    assert settings.backfill_overlap_pages == 3
