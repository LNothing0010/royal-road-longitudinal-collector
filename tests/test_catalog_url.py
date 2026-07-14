from rrlab.catalog import _page_url


def test_page_url_replaces_existing_page_parameter():
    url = _page_url("https://www.royalroad.com/fictions/new?page=1&genre=action", 9)

    assert "page=9" in url
    assert url.count("page=") == 1
    assert "genre=action" in url
