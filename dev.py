"""一键启动开发环境：同时拉起后端(8000) 与前端 Vite(5173)。

用法：`python dev.py`。Ctrl+C 或任一子进程退出时，关闭全部子进程后退出。
纯标准库、不引入新依赖；仅开发便利，不参与打包/测试。
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

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
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        print(
            "[dev] 找不到 npm：请先安装 Node.js 并在 frontend/ 执行 npm install",
            file=sys.stderr,
        )
        sys.exit(1)

    procs = [
        subprocess.Popen([sys.executable, "-m", "nyx.main"], cwd=ROOT),
        subprocess.Popen([npm, "run", "dev"], cwd=FRONTEND),
    ]
    names = ["backend(8000)", "frontend(5173)"]
    print("[dev] 一键启动：backend(8000) + frontend(5173)，Ctrl+C 退出")
    try:
        while True:
            for name, proc in zip(names, procs):
                code = proc.poll()
                if code is not None:
                    print(f"[dev] {name} 已退出 (code={code})，关闭其余…")
                    return
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\n[dev] 收到 Ctrl+C，关闭全部…")
    finally:
        for proc in procs:
            _stop(proc)


if __name__ == "__main__":
    main()
