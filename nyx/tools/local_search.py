import asyncio
import os
import string
from pathlib import Path

from nyx.tools.file_io import is_internal
from nyx.types import Tool

_SEARCH_SUFFIXES = frozenset({".txt", ".md"})
_MAX_RESULTS = 50
_MAX_FILE_BYTES = 1 << 20


def full_disk_roots() -> list[Path]:
    """全盘搜索起点：Windows 枚举存在的盘符，POSIX 返回根目录。"""
    if os.name == "nt":
        letters = string.ascii_uppercase
        return [Path(f"{c}:\\") for c in letters if Path(f"{c}:\\").exists()]
    return [Path("/")]


def _search_local_sync(
    query: str,
    roots: list[Path],
    blocked: list[Path] | tuple[Path, ...] = (),
) -> list[dict[str, str]]:
    """同步核心：os.walk 遍历，onerror 跳过无权限目录，.txt/.md 大小写不敏感子串。

    结果截断到 _MAX_RESULTS、单文件超 _MAX_FILE_BYTES 跳过（全盘扫描兜底）。
    """
    results: list[dict[str, str]] = []
    needle = query.lower()
    for root in roots:
        if is_internal(root, blocked):
            continue
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
            dirnames[:] = [
                name
                for name in dirnames
                if not is_internal(Path(dirpath) / name, blocked)
            ]
            for name in filenames:
                if len(results) >= _MAX_RESULTS:
                    return results
                if Path(name).suffix.lower() not in _SEARCH_SUFFIXES:
                    continue
                full = os.path.join(dirpath, name)
                if is_internal(Path(full), blocked):
                    continue
                try:
                    if os.path.getsize(full) > _MAX_FILE_BYTES:
                        continue
                    text = Path(full).read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                idx = text.lower().find(needle)
                if idx == -1:
                    continue
                start = max(0, idx - 40)
                end = min(len(text), idx + len(needle) + 40)
                results.append({"path": full, "snippet": text[start:end]})
    return results


async def search_local(
    query: str,
    roots: list[Path] | None = None,
    *,
    blocked: list[Path] | tuple[Path, ...] = (),
) -> list[dict[str, str]]:
    """在 roots（缺省 = 全盘）下文本文件中大小写不敏感搜索 query。

    返回 [{path, snippet}]。
    """
    if roots is None:
        roots = full_disk_roots()
    return await asyncio.to_thread(_search_local_sync, query, roots, blocked)


def build_local_search_tool(
    roots: list[Path] | None = None,
    *,
    blocked: list[Path] | tuple[Path, ...] = (),
) -> Tool:
    async def handler(query: str) -> list[dict[str, str]]:
        return await search_local(query, roots, blocked=blocked)

    return Tool(
        name="local_search",
        description="在本地磁盘的文本文件中按关键词搜索，返回匹配文件路径与片段。",
        schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索关键词"}},
            "required": ["query"],
        },
        handler=handler,
    )
