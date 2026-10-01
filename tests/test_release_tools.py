"""CI/CD release-tool regressions."""

import json
import re
import tomllib
from io import BytesIO
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest
import yaml

from scripts.check_release_version import VersionError, validate_versions
from scripts.smoke_release import (
    SmokeError,
    check_json_endpoint,
    check_sse_endpoint,
    release_binaries,
    run_smoke,
    stop_with_stdin_eof,
)

_ROOT = Path(__file__).resolve().parents[1]


def _write_version_sources(root: Path, *, package_version: str = "0.1.0") -> None:
    (root / "frontend" / "src-tauri").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "nyx-agent"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (root / "frontend" / "package.json").write_text(
        json.dumps({"version": package_version}), encoding="utf-8"
    )
    (root / "frontend" / "package-lock.json").write_text(
        json.dumps({"version": "0.1.0"}), encoding="utf-8"
    )
    (root / "frontend" / "src-tauri" / "tauri.conf.json").write_text(
        json.dumps({"version": "0.1.0"}), encoding="utf-8"
    )
    (root / "frontend" / "src-tauri" / "Cargo.toml").write_text(
        '[package]\nname = "nyx"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "frontend" / "src-tauri" / "Cargo.lock").write_text(
        'version = 4\n\n[[package]]\nname = "nyx"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )


def test_validate_versions_accepts_matching_sources_and_tag(tmp_path: Path) -> None:
    _write_version_sources(tmp_path)

    assert validate_versions(tmp_path, "v0.1.0") == "0.1.0"


def test_validate_versions_reports_drift(tmp_path: Path) -> None:
    _write_version_sources(tmp_path, package_version="0.2.0")

    with pytest.raises(VersionError, match="frontend/package.json: 0.2.0"):
        validate_versions(tmp_path)


def test_validate_versions_rejects_nonmatching_tag(tmp_path: Path) -> None:
    _write_version_sources(tmp_path)

    with pytest.raises(VersionError, match="tag v0.2.0 does not match 0.1.0"):
        validate_versions(tmp_path, "v0.2.0")


def test_release_binaries_require_desktop_and_sidecar(tmp_path: Path) -> None:
    (tmp_path / "nyx.exe").touch()

    with pytest.raises(SmokeError, match="nyx-backend.exe"):
        release_binaries(tmp_path)


def test_check_json_endpoint_validates_top_level_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b"{}"
    monkeypatch.setattr(
        "scripts.smoke_release.urlopen", MagicMock(return_value=response)
    )

    check_json_endpoint("http://127.0.0.1:8000/api/state", dict)
    with pytest.raises(SmokeError, match="expected list"):
        check_json_endpoint("http://127.0.0.1:8000/api/events/log", list)


def test_check_sse_endpoint_requires_event_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = MagicMock()
    response.__enter__.return_value = response
    response.headers.get_content_type.return_value = "application/json"
    monkeypatch.setattr(
        "scripts.smoke_release.urlopen", MagicMock(return_value=response)
    )

    with pytest.raises(SmokeError, match="text/event-stream"):
        check_sse_endpoint("http://127.0.0.1:8000/api/events")


def test_stop_with_stdin_eof_closes_pipe_and_waits() -> None:
    process = MagicMock()
    process.stdin = BytesIO()
    process.wait.return_value = 0

    stop_with_stdin_eof(process, timeout=1)

    assert process.stdin.closed
    process.wait.assert_called_once_with(timeout=1)
    process.kill.assert_not_called()


def test_run_smoke_uses_nonsecret_placeholder_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    (release_dir / "nyx.exe").touch()
    (release_dir / "nyx-backend.exe").touch()
    process = MagicMock()
    process.poll.return_value = 0
    captured_env: dict[str, str] = {}

    def start_process(*args: object, **kwargs: object) -> MagicMock:
        captured_env.update(cast(dict[str, str], kwargs["env"]))
        return process

    def no_op(*args: object) -> None:
        return None

    def return_zero(*args: object) -> int:
        return 0

    monkeypatch.setattr("scripts.smoke_release.subprocess.Popen", start_process)
    monkeypatch.setattr("scripts.smoke_release._port_is_open", lambda: False)
    monkeypatch.setattr("scripts.smoke_release._wait_until_ready", no_op)
    monkeypatch.setattr("scripts.smoke_release.check_json_endpoint", no_op)
    monkeypatch.setattr("scripts.smoke_release.check_sse_endpoint", no_op)
    monkeypatch.setattr("scripts.smoke_release.stop_with_stdin_eof", return_zero)

    run_smoke(release_dir, tmp_path / "backend.log", timeout=1)

    assert captured_env["DEEPSEEK_API_KEY"] == "ci-release-smoke-placeholder"


def test_ci_workflow_has_quality_package_and_tag_release_gates() -> None:
    workflow_path = _ROOT / ".github/workflows/ci.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.load(
        workflow_text, Loader=yaml.BaseLoader
    )

    assert set(workflow["on"]) == {"pull_request", "push", "workflow_dispatch"}
    assert set(workflow["jobs"]) == {
        "backend-quality",
        "frontend-desktop-quality",
        "package-windows",
        "release",
    }
    assert set(workflow["jobs"]["package-windows"]["needs"]) == {
        "backend-quality",
        "frontend-desktop-quality",
    }
    assert workflow["jobs"]["release"]["permissions"] == {"contents": "write"}
    assert workflow["jobs"]["package-windows"]["env"]["HF_HOME"] == (
        "${{ runner.temp }}\\huggingface"
    )
    assert "ruff check nyx/ tests/ scripts/" in workflow_text
    assert "pyright nyx/ tests/ scripts/" in workflow_text
    assert "python scripts/smoke_release.py" in workflow_text
    assert "SHA256SUMS.txt" in workflow_text
    assert "--draft" in workflow_text


def test_ci_actions_are_pinned_and_python_dev_dependencies_are_locked() -> None:
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    action_refs = re.findall(r"uses:\s*[^@\s]+@([^\s#]+)", workflow)
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert action_refs and all(
        re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs
    )
    assert set(pyproject["project"]["optional-dependencies"]["dev"]) >= {
        "pytest==9.1.0",
        "pytest-asyncio==1.4.0",
        "pytest-cov==7.1.0",
        "ruff==0.15.20",
        "pyright==1.1.411",
    }
    assert (_ROOT / "uv.lock").is_file()
