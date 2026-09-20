"""Launcher lifecycle and resource packaging regressions."""
# pyright: reportPrivateUsage=false

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import dev
import nyx.main as entry


def test_occupied_backend_port_refuses_before_secret_distribution(
    tmp_path: Path,
) -> None:
    with (
        patch.object(dev.sys, "argv", ["dev.py", "--desktop"]),
        patch.object(dev.shutil, "which", return_value="npm"),
        patch.object(dev, "_BACKEND_PID_FILE", tmp_path / "backend.pid"),
        patch.object(dev, "_LAUNCH_LOCK_FILE", tmp_path / "launcher.lock"),
        patch.object(dev, "_find_launcher_pids", return_value=[]),
        patch.object(dev, "_find_listening_backend_pids", return_value=[]),
        patch.object(dev.socket, "socket") as listener,
        patch.object(dev.subprocess, "Popen") as spawn,
    ):
        listener.return_value.__enter__.return_value.bind.side_effect = OSError()
        with pytest.raises(SystemExit, match="1"):
            dev.main()
    spawn.assert_not_called()


def test_occupied_backend_port_refuses_plain_launcher(tmp_path: Path) -> None:
    with (
        patch.object(dev.sys, "argv", ["dev.py"]),
        patch.object(dev.shutil, "which", return_value="npm"),
        patch.object(dev, "_BACKEND_PID_FILE", tmp_path / "backend.pid"),
        patch.object(dev, "_LAUNCH_LOCK_FILE", tmp_path / "launcher.lock"),
        patch.object(dev, "_find_launcher_pids", return_value=[]),
        patch.object(dev, "_find_listening_backend_pids", return_value=[]),
        patch.object(dev.socket, "socket") as listener,
        patch.object(dev.subprocess, "Popen") as spawn,
    ):
        listener.return_value.__enter__.return_value.bind.side_effect = OSError()
        with pytest.raises(SystemExit, match="1"):
            dev.main()
    spawn.assert_not_called()


def test_launcher_waits_for_backend_ready_before_starting_frontend(
    tmp_path: Path,
) -> None:
    """The frontend must not start while the backend is still booting."""
    ready = threading.Event()
    spawned: list[tuple[str, bool]] = []

    class FakeProcess:
        def __init__(self, pid: int, exit_code: int | None) -> None:
            self.pid = pid
            self.returncode = exit_code

        def poll(self) -> int | None:
            return self.returncode

    backend = FakeProcess(101, None)
    frontend = FakeProcess(102, 1)

    def fake_popen(command: list[str], **_: object) -> FakeProcess:
        if command[1:] == ["-m", "nyx.main"]:
            spawned.append(("backend", ready.is_set()))
            threading.Timer(0.05, ready.set).start()
            return backend
        spawned.append(("frontend", ready.is_set()))
        return frontend

    def wait_for_ready(_proc: object) -> None:
        ready.wait(1)

    with (
        patch.object(dev.sys, "argv", ["dev.py"]),
        patch.object(dev.shutil, "which", return_value="npm"),
        patch.object(dev, "_BACKEND_PID_FILE", tmp_path / "backend.pid"),
        patch.object(dev, "_LAUNCH_LOCK_FILE", tmp_path / "launcher.lock"),
        patch.object(dev, "_stop_legacy_launchers"),
        patch.object(dev, "_stop_previous_backend"),
        patch.object(dev, "_ensure_backend_port_free"),
        patch.object(dev, "_wait_for_backend_ready", side_effect=wait_for_ready),
        patch.object(dev, "_stop"),
        patch.object(dev.subprocess, "Popen", side_effect=fake_popen),
    ):
        with pytest.raises(SystemExit, match="1"):
            dev.main()

    assert spawned == [("backend", False), ("frontend", True)]


def test_launcher_lock_replaces_live_owner_before_restart(tmp_path: Path) -> None:
    lock_file = tmp_path / "launcher.lock"
    lock_file.write_text("1234", encoding="ascii")
    with (
        patch.object(dev, "_LAUNCH_LOCK_FILE", lock_file),
        patch.object(
            dev, "_launcher_process_matches", side_effect=[True, False, False]
        ),
        patch.object(dev.os, "getpid", return_value=5678),
        patch.object(dev.os, "name", "nt"),
        patch.object(dev.subprocess, "run") as run,
    ):
        fd = dev._acquire_launcher_lock()
        try:
            assert lock_file.read_text(encoding="ascii") == "5678"
        finally:
            dev._release_launcher_lock(fd)
    run.assert_called_once_with(
        ["taskkill", "/PID", "1234", "/T", "/F"],
        capture_output=True,
        check=False,
    )


def test_launcher_lock_waits_for_old_lock_handle_to_close(tmp_path: Path) -> None:
    lock_file = tmp_path / "launcher.lock"
    lock_file.write_text("1234", encoding="ascii")
    real_unlink = Path.unlink
    unlink_calls = 0

    def unlink(path: Path, *, missing_ok: bool = False) -> None:
        nonlocal unlink_calls
        if path == lock_file and unlink_calls == 0:
            unlink_calls += 1
            raise PermissionError("lock still closing")
        unlink_calls += 1
        real_unlink(path, missing_ok=missing_ok)

    with (
        patch.object(dev, "_LAUNCH_LOCK_FILE", lock_file),
        patch.object(
            dev, "_launcher_process_matches", side_effect=[True, False, False]
        ),
        patch.object(dev.os, "getpid", return_value=5678),
        patch.object(dev.os, "name", "nt"),
        patch.object(dev.subprocess, "run"),
        patch.object(Path, "unlink", unlink),
        patch.object(dev.time, "sleep"),
    ):
        fd = dev._acquire_launcher_lock()
        try:
            assert lock_file.read_text(encoding="ascii") == "5678"
        finally:
            dev._release_launcher_lock(fd)
    assert unlink_calls == 3


def test_launcher_lock_replaces_stale_owner(tmp_path: Path) -> None:
    lock_file = tmp_path / "launcher.lock"
    lock_file.write_text("1234", encoding="ascii")
    with (
        patch.object(dev, "_LAUNCH_LOCK_FILE", lock_file),
        patch.object(dev, "_launcher_process_matches", return_value=False),
        patch.object(dev.os, "getpid", return_value=5678),
        patch.object(dev.os, "name", "nt"),
    ):
        fd = dev._acquire_launcher_lock()
        try:
            assert lock_file.read_text(encoding="ascii") == "5678"
        finally:
            dev._release_launcher_lock(fd)
    assert not lock_file.exists()


def test_launcher_stops_legacy_launchers(tmp_path: Path) -> None:
    with (
        patch.object(dev, "_find_launcher_pids", return_value=[1234, 5678]),
        patch.object(dev.os, "name", "nt"),
        patch.object(dev.subprocess, "run") as run,
    ):
        dev._stop_legacy_launchers()
    assert run.call_args_list == [
        ((["taskkill", "/PID", "1234", "/T", "/F"],),
         {"capture_output": True, "check": False}),
        ((["taskkill", "/PID", "5678", "/T", "/F"],),
         {"capture_output": True, "check": False}),
    ]


def test_launcher_stops_recorded_backend_before_restart(tmp_path: Path) -> None:
    pid_file = tmp_path / "backend.pid"
    pid_file.write_text("1234", encoding="ascii")
    with (
        patch.object(dev, "_BACKEND_PID_FILE", pid_file),
        patch.object(dev, "_find_listening_backend_pids", return_value=[]),
        patch.object(dev, "_backend_process_matches", return_value=True),
        patch.object(dev.os, "name", "nt"),
        patch.object(dev.subprocess, "run") as run,
    ):
        dev._stop_previous_backend()
    run.assert_called_once_with(
        ["taskkill", "/PID", "1234", "/T", "/F"],
        capture_output=True,
        check=False,
    )
    assert not pid_file.exists()


def test_launcher_stops_legacy_backend_without_pid_file(tmp_path: Path) -> None:
    pid_file = tmp_path / "backend.pid"
    with (
        patch.object(dev, "_BACKEND_PID_FILE", pid_file),
        patch.object(dev, "_find_listening_backend_pids", return_value=[1234, 5678]),
        patch.object(dev, "_backend_process_matches", return_value=True),
        patch.object(dev.os, "name", "nt"),
        patch.object(dev.subprocess, "run") as run,
    ):
        dev._stop_previous_backend()
    assert run.call_args_list == [
        ((["taskkill", "/PID", "1234", "/T", "/F"],),
         {"capture_output": True, "check": False}),
        ((["taskkill", "/PID", "5678", "/T", "/F"],),
         {"capture_output": True, "check": False}),
    ]


def test_launcher_scan_excludes_current_process_ancestors() -> None:
    result = MagicMock(stdout="29840,7084\n30072,29840\n1234,9999\n")
    with (
        patch.object(dev.os, "name", "nt"),
        patch.object(dev.os, "getpid", return_value=30072),
        patch.object(dev.subprocess, "run", return_value=result),
    ):
        assert dev._find_launcher_pids() == [1234]


async def test_packaged_entry_resolves_resources_without_launch_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    (tmp_path / "config.yaml").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.delenv("NYX_CONFIG", raising=False)
    monkeypatch.delenv("NYX_CANON_DIR", raising=False)
    build = AsyncMock(side_effect=RuntimeError("stop before runtime"))
    monkeypatch.setattr(entry, "build_app_context", build)
    with pytest.raises(RuntimeError, match="stop before runtime"):
        await entry.main()
    config = build.call_args.args[0]
    assert config.embedding.model == str(tmp_path / "embedding-model")
    assert entry.os.environ["NYX_CANON_DIR"] == str(tmp_path / "prompts")


def test_sidecar_build_bundles_only_public_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import huggingface_hub

    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev.sys, "argv", ["dev.py", "--build-sidecar"])
    monkeypatch.setattr(
        huggingface_hub, "snapshot_download",
        MagicMock(return_value=str(tmp_path / "model")),
    )
    process = MagicMock(stdout="x86_64-pc-windows-msvc\n")
    with (
        patch.object(dev.subprocess, "run", return_value=process) as run,
        patch.object(
            dev.subprocess, "Popen",
            side_effect=AssertionError("no services during build"),
        ),
        patch.object(dev.shutil, "copy2") as copy,
    ):
        dev.main()
    command = run.call_args_list[-1].args[0]
    assert command[:3] == [dev.sys.executable, "-m", "PyInstaller"]
    assert f"{tmp_path / 'prompts'};prompts" in command
    assert not any(".env" in argument for argument in command)
    assert "x86_64-pc-windows-msvc" in str(copy.call_args.args[1])


