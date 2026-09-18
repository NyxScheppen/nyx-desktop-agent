"""一键启动后端(8000) 与 Vite(5173)，或配对的 Tauri 桌面开发环境。

用法：`python dev.py` / `python dev.py --desktop` / `python dev.py --build-sidecar`。
Ctrl+C 或任一子进程退出时，关闭本 launcher 的全部子进程后退出；不参与打包。
"""

import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"


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
    launch_env = os.environ.copy()
    launch_env.pop("NYX_BROWSER_BOOTSTRAP_SECRET", None)
    if desktop:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 8000))
        except OSError:
            print("[dev] 8000 端口已占用，拒绝桌面配对启动", file=sys.stderr)
            sys.exit(1)
        launch_env["NYX_BROWSER_BOOTSTRAP_SECRET"] = secrets.token_hex(32)
    procs: list[subprocess.Popen[bytes]] = []
    names = ["backend(8000)", "desktop" if desktop else "frontend(5173)"]
    failed = False
    print(f"[dev] 一键启动：{' + '.join(names)}，Ctrl+C 退出")
    try:
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "nyx.main"], cwd=ROOT, env=launch_env,
        ))
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
    # Preserve a non-zero exit status so wrappers (for example start_nyx.bat)
    # can report backend/frontend startup failures accurately.
    if failed or any(proc.returncode not in (None, 0) for proc in procs):
        sys.exit(1)


if __name__ == "__main__":
    main()
