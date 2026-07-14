from pathlib import Path


def test_catalog_collector_has_no_chapter_text_storage_contract():
    catalog_source = Path("rrlab/catalog.py").read_text(encoding="utf-8")

    assert "chapter_body" not in catalog_source
    assert "chapter_text" not in catalog_source
