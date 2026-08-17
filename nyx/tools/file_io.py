import asyncio
from pathlib import Path
from typing import Any

from nyx.types import Tool

DEFAULT_WRITE_ROOT = Path("workspace")


def _resolve_write(root: Path, path: str) -> Path:
    """把 write 的 path 解析到 root 内；越界或指向 root 本身抛 ValueError。"""
    root_resolved = root.resolve()
    p = Path(path)
    resolved = p.resolve() if p.is_absolute() else (root_resolved / p).resolve()
    if not resolved.is_relative_to(root_resolved):
        raise ValueError(f"写入路径越界：{path!r}（仅允许 {root_resolved} 内）")
    if resolved == root_resolved:
        raise ValueError(f"写入路径无效：{path!r} 指向 write_root 本身")
    return resolved


async def file_io(
    action: str,
    path: str,
    content: str | None = None,
    write_root: Path = DEFAULT_WRITE_ROOT,
) -> dict[str, Any]:
    """read 全盘读 / write 写进 write_root / list 列目录（全盘）。"""
    if action == "read":
        text = await asyncio.to_thread(
            Path(path).read_text, encoding="utf-8", errors="replace"
        )
        return {"path": path, "content": text}
    if action == "write":
        target = _resolve_write(write_root, path)

        def _do() -> int:
            target.parent.mkdir(parents=True, exist_ok=True)
            return target.write_text(content or "", encoding="utf-8")

        return {"path": str(target), "written": await asyncio.to_thread(_do)}
    if action == "list":
        return {"path": path, "entries": await asyncio.to_thread(_list_entries, path)}
    raise ValueError(f"未知 file_io 动作：{action!r}（应为 read/write/list）")


def _list_entries(path: str) -> list[str]:
    return [p.name for p in Path(path).iterdir()]


def build_file_io_tool(write_root: Path = DEFAULT_WRITE_ROOT) -> Tool:
    async def handler(
        action: str, path: str, content: str | None = None
    ) -> dict[str, Any]:
        return await file_io(action, path, content, write_root)

    return Tool(
        name="file_io",
        description="读写本地文件：read 全盘读文本、write 写进工作目录、list 列目录。",
        schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read", "write", "list"]},
                "path": {"type": "string", "description": "文件或目录路径"},
                "content": {"type": "string", "description": "write 时写入的文本"},
            },
            "required": ["action", "path"],
        },
        handler=handler,
    )
