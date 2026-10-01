"""Pytest setup: repo root on the path, quiet logs, the ``live`` marker.

Tests marked ``live`` call real services (the Coda LLM) and are skipped
unless RUN_LIVE_TESTS=1.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from loguru import logger

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

logger.remove()
logger.add(sys.stderr, level=os.environ.get("TEST_LOG_LEVEL", "WARNING"))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: calls real services; set RUN_LIVE_TESTS=1 to run")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("RUN_LIVE_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="live test; set RUN_LIVE_TESTS=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
