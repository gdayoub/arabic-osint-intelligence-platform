"""Run the small Worker boundary check inside the existing unit-test CI path."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_public_archive_cors_is_narrow():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable; run node --test tests/atlas-worker.test.mjs separately")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [node, "--test", "tests/atlas-worker.test.mjs"],
        cwd=root, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
