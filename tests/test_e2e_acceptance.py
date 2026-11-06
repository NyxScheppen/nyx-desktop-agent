"""Run the previously external HTTP/SSE and persistence acceptance scenarios."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = sorted(
    script
    for script in (_ROOT / "scripts" / "e2e").glob("nyx-p*.py")
    if not script.name.endswith("-red.py")
)
_ENV = os.environ.copy()
_ENV["NYX_AUTH_TOKEN"] = ""
_ENV["NYX_DATABASE_URL"] = ""
_ENV.pop("NYX_PUBLIC_ORIGIN", None)


@pytest.mark.parametrize("script", _SCRIPTS, ids=lambda script: script.stem)
def test_historical_e2e_scenario_passes(script: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=_ROOT,
        env=_ENV,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert result.returncode == 0, (
        f"{script.name} failed:\n{result.stdout}\n{result.stderr}"
    )
