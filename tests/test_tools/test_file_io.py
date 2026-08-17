from pathlib import Path

import pytest

from nyx.tools.file_io import file_io


async def test_read(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    result = await file_io("read", str(f))
    assert result == {"path": str(f), "content": "hello"}


async def test_write(tmp_path: Path) -> None:
    result = await file_io("write", "note.txt", "hi", write_root=tmp_path)
    assert result["written"] == 2
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hi"


async def test_write_escape_parent(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        await file_io("write", "../evil.txt", "x", write_root=tmp_path)


async def test_write_escape_absolute(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    with pytest.raises(ValueError):
        await file_io("write", str(outside), "x", write_root=root)


async def test_list(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("y", encoding="utf-8")
    result = await file_io("list", str(tmp_path))
    assert set(result["entries"]) == {"a.txt", "b.txt"}


async def test_unknown_action() -> None:
    with pytest.raises(ValueError):
        await file_io("delete", "x")
