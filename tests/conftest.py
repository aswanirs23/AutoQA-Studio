"""Shared pytest fixtures for the AutoQA Studio backend."""

import pytest


@pytest.fixture
def sample_session_id() -> str:
    """A deterministic session ID for tests that derive feature names from it."""
    return "bs_abc12345def6"
