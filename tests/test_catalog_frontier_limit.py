from rrlab.config import Settings


def test_frontier_page_ceiling_is_positive_and_bounded():
    settings = Settings()

    assert 2 <= settings.newest_max_pages <= 100
