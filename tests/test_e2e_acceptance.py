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
if not _SCRIPTS:
    raise RuntimeError("no acceptance drivers found")

_ENV = os.environ.copy()
_ENV["PYTHONIOENCODING"] = "utf-8"
_ENV["NYX_AUTH_TOKEN"] = ""
_ENV["NYX_DATABASE_URL"] = ""
_ENV.pop("NYX_PUBLIC_ORIGIN", None)


@pytest.mark.parametrize("script", _SCRIPTS, ids=lambda script: script.stem)
def test_historical_e2e_scenario_passes(script: Path) -> None:
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=_ROOT,
            env=_ENV,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        output = "\n".join(
            value.decode(errors="replace") if isinstance(value, bytes) else value or ""
            for value in (error.stdout, error.stderr)
        )
        pytest.fail(f"{script.name} timed out after {error.timeout}s:\n{output}")

    assert result.returncode == 0, (
        f"{script.name} failed:\n{result.stdout}\n{result.stderr}"
    )
