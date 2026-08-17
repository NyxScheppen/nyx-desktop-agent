import asyncio
import os
import string
from pathlib import Path

from nyx.types import Tool

_SEARCH_SUFFIXES = frozenset({".txt", ".md"})


def full_disk_roots() -> list[Path]:
    """全盘搜索起点：Windows 枚举存在的盘符，POSIX 返回根目录。"""
    if os.name == "nt":
        letters = string.ascii_uppercase
        return [Path(f"{c}:\\") for c in letters if Path(f"{c}:\\").exists()]
    return [Path("/")]


def _search_local_sync(query: str, roots: list[Path]) -> list[dict[str, str]]:
    """同步核心：os.walk 遍历，onerror 跳过无权限目录，.txt/.md 大小写不敏感子串。"""
    results: list[dict[str, str]] = []
    needle = query.lower()
    for root in roots:
        for dirpath, _dirnames, filenames in os.walk(root, onerror=lambda _e: None):
            for name in filenames:
                if Path(name).suffix.lower() not in _SEARCH_SUFFIXES:
                    continue
                full = os.path.join(dirpath, name)
                try:
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
    query: str, roots: list[Path] | None = None
) -> list[dict[str, str]]:
    """在 roots（缺省 = 全盘）下文本文件中大小写不敏感搜索 query。

    返回 [{path, snippet}]。
    """
    if roots is None:
        roots = full_disk_roots()
    return await asyncio.to_thread(_search_local_sync, query, roots)


def build_local_search_tool(roots: list[Path] | None = None) -> Tool:
    async def handler(query: str) -> list[dict[str, str]]:
        return await search_local(query, roots)

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
