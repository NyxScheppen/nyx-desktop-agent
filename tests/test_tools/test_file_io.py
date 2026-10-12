import asyncio
import threading
from io import BytesIO
from pathlib import Path

import pytest

from nyx.tools.file_io import file_io


async def test_cancel_write_waits_for_actual_disk_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, release = threading.Event(), threading.Event()
    original = Path.write_text

    def slow_write(path: Path, data: str, **kwargs: object) -> int:
        started.set()
        release.wait(5)
        return original(path, data, encoding="utf-8")

    monkeypatch.setattr(Path, "write_text", slow_write)
    task = asyncio.create_task(file_io("write", "late.txt", "old", tmp_path))
    try:
        await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert (tmp_path / "late.txt").read_text(encoding="utf-8") == "old"


class _TrackingReader(BytesIO):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.read_sizes: list[int] = []

    def read(self, size: int | None = -1) -> bytes:
        self.read_sizes.append(-1 if size is None else size)
        return super().read(size)


async def test_read(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    result = await file_io("read", str(f))
    assert result == {"path": str(f), "content": "hello"}


async def test_read_non_utf8_replaces(tmp_path: Path) -> None:
    f = tmp_path / "gbk.txt"
    f.write_bytes(b"\xffA")  # \xff 非合法 UTF-8 首字节
    result = await file_io("read", str(f))
    assert result["content"] == "�A"


async def test_write(tmp_path: Path) -> None:
    result = await file_io("write", "note.txt", "hi", write_root=tmp_path)
    assert result["written"] == 2
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hi"


async def test_write_same_content_does_not_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "note.txt"
    target.write_text("hi", encoding="utf-8")

    def fail_write(*args: object, **kwargs: object) -> int:
        raise AssertionError("相同内容不应重复写盘")

    monkeypatch.setattr(Path, "write_text", fail_write)
    result = await file_io("write", "note.txt", "hi", write_root=tmp_path)
    assert result["written"] == 2


async def test_same_content_comparison_reads_in_bounded_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"x" * (256 * 1024 + 1)
    target = tmp_path / "large.txt"
    target.write_bytes(payload)
    reader = _TrackingReader(payload)

    def tracking_open(path: Path, mode: str = "r") -> _TrackingReader:
        assert path == target
        assert mode == "rb"
        return reader

    monkeypatch.setattr(Path, "open", tracking_open)
    result = await file_io(
        "write", target.name, payload.decode("ascii"), write_root=tmp_path
    )

    assert result["written"] == len(payload)
    assert reader.read_sizes
    assert all(0 < size < len(payload) for size in reader.read_sizes)


async def test_write_replaces_non_utf8_content(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_bytes(b"\xff")
    result = await file_io("write", "note.txt", "hi", write_root=tmp_path)
    assert result["written"] == 2
    assert target.read_text(encoding="utf-8") == "hi"


async def test_write_escape_parent(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        await file_io("write", "../evil.txt", "x", write_root=tmp_path)


async def test_write_escape_absolute(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    with pytest.raises(ValueError):
        await file_io("write", str(outside), "x", write_root=root)


async def test_write_empty_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        await file_io("write", "", "x", write_root=tmp_path)


async def test_write_escape_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前环境不支持创建符号链接")
    with pytest.raises(ValueError):
        await file_io("write", "link.txt", "x", write_root=root)


async def test_list(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("y", encoding="utf-8")
    result = await file_io("list", str(tmp_path))
    assert set(result["entries"]) == {"a.txt", "b.txt"}


async def test_unknown_action() -> None:
    with pytest.raises(ValueError):
        await file_io("delete", "x")


def test_process_shutdown_finishes_thread_write(tmp_path: Path) -> None:
    import subprocess
    import sys

    script = """
import asyncio, time
from pathlib import Path
from nyx.tools.file_io import file_io
original = Path.write_text
def slow(self, *args, **kwargs):
    time.sleep(0.2)
    return original(self, *args, **kwargs)
Path.write_text = slow
async def main():
    root = Path(__import__('sys').argv[1])
    asyncio.create_task(file_io('write', 'done.txt', 'saved', write_root=root))
    await asyncio.sleep(0.05)
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], timeout=5, capture_output=True
    )
    assert result.returncode == 0, result.stderr.decode()
    assert (tmp_path / "done.txt").read_text(encoding="utf-8") == "saved"
