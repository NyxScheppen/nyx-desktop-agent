"""Smoke-test the frozen backend shipped beside the Tauri executable."""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

_BASE_URL = "http://127.0.0.1:8000"
_MAX_RESPONSE_BYTES = 1 << 20
_REQUEST_TIMEOUT = 5.0


class SmokeError(RuntimeError):
    """Raised when a release artifact fails a smoke assertion."""


def release_binaries(release_dir: Path) -> tuple[Path, Path]:
    """Return the desktop and sidecar binaries after validating layout."""
    desktop = release_dir / "nyx.exe"
    sidecar = release_dir / "nyx-backend.exe"
    missing = [path.name for path in (desktop, sidecar) if not path.is_file()]
    if missing:
        raise SmokeError(f"missing release binaries: {', '.join(missing)}")
    return desktop, sidecar


def check_json_endpoint(url: str, expected_type: type[object]) -> None:
    """Require a bounded JSON response with the expected top-level type."""
    with urlopen(url, timeout=_REQUEST_TIMEOUT) as response:  # noqa: S310
        body = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(body) > _MAX_RESPONSE_BYTES:
        raise SmokeError(f"{url} response exceeds {_MAX_RESPONSE_BYTES} bytes")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SmokeError(f"{url} did not return valid JSON") from error
    if not isinstance(payload, expected_type):
        raise SmokeError(f"{url} expected {expected_type.__name__}")


def check_sse_endpoint(url: str) -> None:
    """Require the release event endpoint to negotiate an SSE response."""
    with urlopen(url, timeout=_REQUEST_TIMEOUT) as response:  # noqa: S310
        content_type = response.headers.get_content_type()
    if content_type != "text/event-stream":
        raise SmokeError(f"{url} expected text/event-stream, got {content_type}")


def stop_with_stdin_eof(
    process: subprocess.Popen[bytes], *, timeout: float = 35.0
) -> int:
    """Request the frozen backend's normal shutdown, killing only on timeout."""
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.wait()
        raise SmokeError("packaged backend did not stop after stdin EOF") from error


def _port_is_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 8000), timeout=0.2):
            return True
    except OSError:
        return False


def _wait_until_ready(process: subprocess.Popen[bytes], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            raise SmokeError(f"packaged backend exited during startup (code={code})")
        try:
            check_json_endpoint(f"{_BASE_URL}/api/state", dict)
            return
        except (OSError, URLError, SmokeError) as error:
            last_error = error
            time.sleep(0.2)
    raise SmokeError(f"packaged backend did not become ready: {last_error}")


def run_smoke(release_dir: Path, log_path: Path, timeout: float) -> None:
    """Run the release sidecar and verify API, SSE, and shutdown behavior."""
    _, sidecar = release_binaries(release_dir)
    if _port_is_open():
        raise SmokeError("port 8000 is already occupied")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["DEEPSEEK_API_KEY"] = "ci-release-smoke-placeholder"
    succeeded = False
    process: subprocess.Popen[bytes] | None = None
    with tempfile.TemporaryDirectory(prefix="nyx-release-smoke-") as temp:
        env["NYX_DB"] = str(Path(temp) / "nyx.db")
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [str(sidecar)],
                cwd=temp,
                env=env,
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                _wait_until_ready(process, timeout)
                check_json_endpoint(f"{_BASE_URL}/api/events/log", list)
                check_sse_endpoint(f"{_BASE_URL}/api/events")
                if stop_with_stdin_eof(process) != 0:
                    raise SmokeError("packaged backend returned a non-zero exit code")
                deadline = time.monotonic() + 5.0
                while _port_is_open() and time.monotonic() < deadline:
                    time.sleep(0.1)
                if _port_is_open():
                    raise SmokeError("port 8000 remained occupied after shutdown")
                succeeded = True
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
    if succeeded:
        log_path.unlink(missing_ok=True)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=root / "frontend/src-tauri/target/release",
    )
    parser.add_argument("--log-path", type=Path, default=root / "artifacts/backend.log")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    try:
        run_smoke(args.release_dir.resolve(), args.log_path.resolve(), args.timeout)
    except (OSError, SmokeError) as error:
        print(f"release smoke failed: {error}", file=sys.stderr)
        return 1
    print("release smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
