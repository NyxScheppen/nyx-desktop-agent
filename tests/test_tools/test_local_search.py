import os
from pathlib import Path

from nyx.tools.local_search import full_disk_roots, search_local


async def test_search_hits(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("the deep sea is vast", encoding="utf-8")
    results = await search_local("deep", [tmp_path])
    assert len(results) == 1
    assert results[0]["path"].endswith("a.md")
    assert "deep" in results[0]["snippet"]


async def test_search_miss(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("nothing here", encoding="utf-8")
    assert await search_local("xyz", [tmp_path]) == []


async def test_search_empty_roots() -> None:
    assert await search_local("x", []) == []


async def test_search_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("deep sea", encoding="utf-8")
    results = await search_local("DEEP", [tmp_path])
    assert len(results) == 1


async def test_search_skips_non_text(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("needle here", encoding="utf-8")
    (tmp_path / "b.py").write_text("needle here", encoding="utf-8")
    results = await search_local("needle", [tmp_path])
    assert [Path(r["path"]).name for r in results] == ["a.txt"]


def test_full_disk_roots_nonempty_and_exists() -> None:
    roots = full_disk_roots()
    assert roots
    assert all(r.exists() for r in roots)
    if os.name != "nt":
        assert roots == [Path("/")]
