"""一键启动后端(8000) 与 Vite(5173)，或配对的 Tauri 桌面开发环境。

用法：`python dev.py` / `python dev.py --desktop` / `python dev.py --build-sidecar`。
Ctrl+C 或任一子进程退出时，关闭本 launcher 的全部子进程后退出；不参与打包。
"""

import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
_BACKEND_ADDRESS = ("127.0.0.1", 8000)
_BACKEND_STARTUP_TIMEOUT = 120.0
_BACKEND_PROBE_TIMEOUT = 0.2
_BACKEND_PID_FILE = ROOT / ".nyx-backend.pid"
_LAUNCH_LOCK_FILE = ROOT / ".nyx-launcher.lock"


def _stop(proc: subprocess.Popen[bytes]) -> None:
    """关闭单个子进程；Windows 整棵进程树一并杀（npm.cmd→node 不残留孤儿）。"""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _ensure_backend_port_free() -> None:
    """Refuse a second launcher before it can share the SQLite database."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(_BACKEND_ADDRESS)
    except OSError:
        print(
            "[dev] 8000 端口已占用，拒绝重复启动；请先关闭已有 Nyx 后端。",
            file=sys.stderr,
        )
        sys.exit(1)


def _wait_for_backend_ready(proc: subprocess.Popen[bytes]) -> None:
    """Wait until the owned backend accepts loopback connections."""
    deadline = time.monotonic() + _BACKEND_STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"backend exited before becoming ready (code={proc.returncode})"
            )
        try:
            with socket.create_connection(
                _BACKEND_ADDRESS, timeout=_BACKEND_PROBE_TIMEOUT
            ):
                return
        except OSError:
            time.sleep(_BACKEND_PROBE_TIMEOUT)
    raise RuntimeError("backend did not become ready before the startup timeout")


def _backend_process_matches(pid: int) -> bool:
    """Check that a PID file still points at a Nyx backend process."""
    if os.name == "nt":
        query = (
            "$p=Get-CimInstance Win32_Process -Filter 'ProcessId = %d'; "
            "if ($p -and $p.CommandLine -match 'nyx\\.main') { exit 0 } "
            "else { exit 1 }"
        ) % pid
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", query],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    try:
        command_line = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return b"nyx.main" in command_line


def _launcher_process_matches(pid: int) -> bool:
    """Check that a launcher lock owner is still a project launcher."""
    if os.name == "nt":
        query = (
            "$p=Get-CimInstance Win32_Process -Filter 'ProcessId = %d'; "
            "if ($p -and $p.CommandLine -match 'dev\\.py') { exit 0 } "
            "else { exit 1 }"
        ) % pid
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", query],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    try:
        command_line = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return b"dev.py" in command_line


def _find_launcher_pids() -> list[int]:
    """Find older Python launchers that predate the atomic lock file."""
    if os.name != "nt":
        return []
    query = (
        "Get-CimInstance Win32_Process | Where-Object "
        "{ $_.Name -match 'python' -and "
        "$_.CommandLine -match '(^|[\\\" ])dev\\.py([\\\" ]|$)' } | "
        "ForEach-Object { \"$($_.ProcessId),$($_.ParentProcessId)\" }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", query],
        capture_output=True,
        text=True,
        check=False,
    )
    parent_by_pid: dict[int, int] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(",")
        if len(parts) != 2:
            continue
        try:
            pid, parent_pid = (int(part) for part in parts)
        except ValueError:
            continue
        parent_by_pid[pid] = parent_pid
    ancestors = {os.getpid()}
    current = os.getpid()
    while current in parent_by_pid:
        parent = parent_by_pid[current]
        if parent in ancestors:
            break
        ancestors.add(parent)
        current = parent
    pids = [pid for pid in parent_by_pid if pid not in ancestors]
    return pids


def _stop_legacy_launchers() -> None:
    """Stop launchers left by versions without the atomic lock."""
    for pid in _find_launcher_pids():
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            os.kill(pid, signal.SIGTERM)


def _acquire_launcher_lock() -> int:
    """Prevent races by stopping a previous live launcher before takeover."""
    while True:
        try:
            fd = os.open(
                _LAUNCH_LOCK_FILE,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            try:
                owner_pid = int(_LAUNCH_LOCK_FILE.read_text(encoding="ascii").strip())
            except (FileNotFoundError, ValueError):
                print(
                    "[dev] 已有启动器正在初始化，请稍后重试。",
                    file=sys.stderr,
                )
                sys.exit(1)
            deadline = time.monotonic() + 5.0
            if _launcher_process_matches(owner_pid):
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(owner_pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                    )
                else:
                    os.kill(owner_pid, signal.SIGTERM)
                while (
                    _launcher_process_matches(owner_pid)
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.05)
                if _launcher_process_matches(owner_pid):
                    print(
                        "[dev] 无法关闭旧 Nyx 启动器，请稍后重试。",
                        file=sys.stderr,
                    )
                    sys.exit(1)
            while True:
                try:
                    _LAUNCH_LOCK_FILE.unlink(missing_ok=True)
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        print(
                            "[dev] 旧启动器锁定文件未释放，请稍后重试。",
                            file=sys.stderr,
                        )
                        sys.exit(1)
                    time.sleep(0.05)
            continue
        os.write(fd, str(os.getpid()).encode("ascii"))
        return fd


def _release_launcher_lock(fd: int) -> None:
    os.close(fd)
    try:
        owner_pid = int(_LAUNCH_LOCK_FILE.read_text(encoding="ascii").strip())
    except (FileNotFoundError, ValueError):
        return
    if owner_pid == os.getpid():
        _LAUNCH_LOCK_FILE.unlink(missing_ok=True)


def _find_listening_backend_pids() -> list[int]:
    """Find all legacy Nyx backends when they predate the PID file."""
    if os.name != "nt":
        return []
    query = (
        "$ids=Get-NetTCPConnection -LocalPort 8000 -State Listen "
        "-ErrorAction SilentlyContinue | Select-Object -Expand OwningProcess -Unique; "
        "foreach ($id in $ids) { $p=Get-CimInstance Win32_Process "
        "-Filter ('ProcessId = ' + $id); "
        "if ($p -and $p.CommandLine -match 'nyx\\.main') "
        "{ Write-Output $id } }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", query],
        capture_output=True,
        text=True,
        check=False,
    )
    pids: list[int] = []
    for line in result.stdout.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid not in pids:
            pids.append(pid)
    return pids


def _stop_previous_backend() -> None:
    """Stop known Nyx backends before this launcher binds the shared port."""
    try:
        pid = int(_BACKEND_PID_FILE.read_text(encoding="ascii").strip())
    except (FileNotFoundError, ValueError):
        pid = None
    candidates = _find_listening_backend_pids()
    if pid is not None and pid not in candidates:
        candidates.insert(0, pid)
    for candidate in candidates:
        if not _backend_process_matches(candidate):
            continue
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(candidate), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            os.kill(candidate, signal.SIGTERM)
    _BACKEND_PID_FILE.unlink(missing_ok=True)


def _remember_backend(pid: int) -> None:
    _BACKEND_PID_FILE.write_text(str(pid), encoding="ascii")


def _forget_backend(pid: int) -> None:
    try:
        if _BACKEND_PID_FILE.read_text(encoding="ascii").strip() == str(pid):
            _BACKEND_PID_FILE.unlink()
    except FileNotFoundError:
        pass


def main() -> None:
    if "--build-sidecar" in sys.argv[1:]:
        import huggingface_hub

        target = subprocess.run(
            ["rustc", "--print", "host-tuple"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        download = cast(
            Callable[..., str], getattr(huggingface_hub, "snapshot_download")
        )
        model = download(
            repo_id="sentence-transformers/all-MiniLM-L6-v2",
            allow_patterns=["*.json", "*.txt", "model.safetensors"],
        )
        output = ROOT / "build" / "sidecar"
        separator = ";" if os.name == "nt" else ":"
        command = [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir",
            "--name", "nyx-backend", "--distpath", str(output),
            "--workpath", str(ROOT / "build" / "pyinstaller"),
            "--specpath", str(ROOT / "build"), "--paths", str(ROOT),
            "--add-data", f"{ROOT / 'config.yaml'}{separator}.",
            "--add-data", f"{ROOT / 'prompts'}{separator}prompts",
            "--add-data", f"{model}{separator}embedding-model",
            "--collect-submodules", "sentence_transformers",
            "--copy-metadata", "sentence-transformers",
            "--collect-submodules", "transformers.models.bert",
        ]
        for module in (
            "tensorflow", "jax", "matplotlib", "IPython", "pytest",
            "torchvision", "torchaudio", "cv2", "tkinter",
        ):
            command.extend(["--exclude-module", module])
        command.append(str(ROOT / "nyx" / "main.py"))
        subprocess.run(command, cwd=ROOT, check=True)
        extension = ".exe" if os.name == "nt" else ""
        folder = output / "nyx-backend"
        shutil.copy2(
            folder / f"nyx-backend{extension}",
            folder / f"nyx-backend-{target}{extension}",
        )
        return

    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        print(
            "[dev] 找不到 npm：请先安装 Node.js 并在 frontend/ 执行 npm install",
            file=sys.stderr,
        )
        sys.exit(1)

    desktop = "--desktop" in sys.argv[1:]
    lock_fd = _acquire_launcher_lock()
    try:
        _stop_legacy_launchers()
        _stop_previous_backend()
        _ensure_backend_port_free()
        launch_env = os.environ.copy()
        launch_env.pop("NYX_BROWSER_BOOTSTRAP_SECRET", None)
        if desktop:
            launch_env["NYX_BROWSER_BOOTSTRAP_SECRET"] = secrets.token_hex(32)
        procs: list[subprocess.Popen[bytes]] = []
        names = ["backend(8000)", "desktop" if desktop else "frontend(5173)"]
        failed = False
        print(f"[dev] 一键启动：{' + '.join(names)}，Ctrl+C 退出")
        try:
            backend = subprocess.Popen(
                [sys.executable, "-m", "nyx.main"], cwd=ROOT, env=launch_env,
            )
            procs.append(backend)
            backend_pid = cast(object, backend.pid)
            if isinstance(backend_pid, int):
                _remember_backend(backend_pid)
            print("[dev] 后端启动中，等待 8000 端口就绪…")
            try:
                _wait_for_backend_ready(backend)
            except RuntimeError as exc:
                print(f"[dev] 后端未就绪：{exc}")
                failed = True
            else:
                procs.append(subprocess.Popen(
                    [npm, "run", "tauri", "dev"] if desktop else [npm, "run", "dev"],
                    cwd=FRONTEND, env=launch_env,
                ))
                while True:
                    for name, proc in zip(names, procs):
                        code = proc.poll()
                        if code is not None:
                            print(f"[dev] {name} 已退出 (code={code})，关闭其余…")
                            failed = code != 0
                            break
                    if failed or any(proc.poll() is not None for proc in procs):
                        break
                    time.sleep(0.3)
        except KeyboardInterrupt:
            print("\n[dev] 收到 Ctrl+C，关闭全部…")
        finally:
            for proc in procs:
                _stop(proc)
            if procs:
                backend_pid = procs[0].pid
                _forget_backend(backend_pid)
        # Preserve a non-zero exit status so wrappers (for example start_nyx.bat)
        # can report backend/frontend startup failures accurately.
        if failed or any(proc.returncode not in (None, 0) for proc in procs):
            sys.exit(1)
    finally:
        _release_launcher_lock(lock_fd)


if __name__ == "__main__":
    main()
