from pathlib import Path


def test_conftest_store_fixture(store):
    assert store.layout.db_path.exists()
    workers = store.list_workers()
    assert workers == []
