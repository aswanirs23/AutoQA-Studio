from backend.services.snapshot_cache import (
    get_cached_snapshot, set_cached_snapshot, clear_snapshot_cache,
    SNAPSHOT_TTL_SECONDS,
)


def test_miss_returns_none():
    clear_snapshot_cache()
    assert get_cached_snapshot("http://x", "/") is None


def test_set_then_get_hits():
    clear_snapshot_cache()
    set_cached_snapshot("http://x", "/a", "SNAP", now=100.0)
    assert get_cached_snapshot("http://x", "/a", now=101.0) == "SNAP"


def test_expired_entry_returns_none():
    clear_snapshot_cache()
    set_cached_snapshot("http://x", "/a", "SNAP", now=100.0)
    later = 100.0 + SNAPSHOT_TTL_SECONDS + 1.0
    assert get_cached_snapshot("http://x", "/a", now=later) is None


def test_distinct_keys_do_not_collide():
    clear_snapshot_cache()
    set_cached_snapshot("http://x", "/a", "A", now=100.0)
    set_cached_snapshot("http://x", "/b", "B", now=100.0)
    assert get_cached_snapshot("http://x", "/a", now=100.0) == "A"
    assert get_cached_snapshot("http://x", "/b", now=100.0) == "B"
