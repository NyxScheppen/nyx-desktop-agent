"""Run every repository acceptance driver in a stable order."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def acceptance_scripts(root: Path) -> list[Path]:
    return sorted(
        script
        for script in (root / "scripts" / "e2e").glob("nyx-p*.py")
        if not script.name.endswith("-red.py")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    scripts = acceptance_scripts(root)
    if not scripts:
        parser.error("no acceptance drivers found")
    env = os.environ.copy()
    # Historical drivers build isolated in-memory apps without API auth.
    env["NYX_AUTH_TOKEN"] = ""
    env["NYX_DATABASE_URL"] = ""
    env.pop("NYX_PUBLIC_ORIGIN", None)
    for script in scripts:
        print(f"==> {script.relative_to(root)}", flush=True)
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=root,
            env=env,
            timeout=args.timeout,
            check=False,
        )
        if result.returncode:
            print(f"FAILED: {script.name} ({result.returncode})", flush=True)
            return result.returncode
    print(f"PASS: {len(scripts)} acceptance drivers", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
