"""In-memory TTL cache for pre-generation page snapshots.

Single-worker process (per CLAUDE.md) so a module-level dict is safe. Keyed by
(base_url, landing_path); a snapshot is page-structural, not test-specific, so
all test cases for one feature share one capture.
"""

from __future__ import annotations

import time

SNAPSHOT_TTL_SECONDS = 300.0

# key: (base_url, landing_path) -> (captured_at_monotonic, snapshot)
_CACHE: dict[tuple[str, str], tuple[float, str]] = {}


def _now(now: float | None) -> float:
    return time.monotonic() if now is None else now


def get_cached_snapshot(base_url: str, landing_path: str, now: float | None = None) -> str | None:
    entry = _CACHE.get((base_url, landing_path))
    if entry is None:
        return None
    captured_at, snapshot = entry
    if _now(now) - captured_at > SNAPSHOT_TTL_SECONDS:
        _CACHE.pop((base_url, landing_path), None)
        return None
    return snapshot


def set_cached_snapshot(base_url: str, landing_path: str, snapshot: str, now: float | None = None) -> None:
    _CACHE[(base_url, landing_path)] = (_now(now), snapshot)


def clear_snapshot_cache() -> None:
    _CACHE.clear()
