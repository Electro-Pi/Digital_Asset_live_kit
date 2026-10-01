"""Script-style check files, run under pytest."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("script", ["tests/catalog_checks.py"])
def test_script_passes(script):
    result = subprocess.run([sys.executable, script], cwd=ROOT, capture_output=True, text=True, env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
