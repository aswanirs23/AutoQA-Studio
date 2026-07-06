"""Duplicate detection for test cases (hash-based MVP).

Algorithm:
- Normalize title (lowercase strip) + steps (lowercase, JSON-sorted list).
- SHA-256 hex digest = canonical fingerprint.
- filter_duplicates drops candidates whose hash exists in session or in the same batch.

Future: optional embedding similarity (not implemented here).
"""

import hashlib
import json

from backend.models.test_case import TestCase


def compute_hash(title: str, steps: list[str]) -> str:
    normalized_title = (title or "").strip().lower()
    normalized_steps = json.dumps([s.strip().lower() for s in steps], sort_keys=True)
    payload = f"{normalized_title}|{normalized_steps}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def with_hash(tc: TestCase) -> TestCase:
    h = compute_hash(tc.title, tc.steps)
    return tc.model_copy(update={"hash": h})


def filter_duplicates(
    candidates: list[TestCase],
    existing_hashes: set[str],
) -> tuple[list[TestCase], int]:
    """Return (new_cases, skipped_count)."""
    new_list: list[TestCase] = []
    skipped = 0
    seen_new: set[str] = set()
    for tc in candidates:
        tc = with_hash(tc)
        if tc.hash in existing_hashes or tc.hash in seen_new:
            skipped += 1
            continue
        seen_new.add(tc.hash)
        new_list.append(tc)
    return new_list, skipped


def existing_hash_set(test_cases: list[TestCase]) -> set[str]:
    return {tc.hash for tc in test_cases if tc.hash} | {
        compute_hash(tc.title, tc.steps) for tc in test_cases
    }
