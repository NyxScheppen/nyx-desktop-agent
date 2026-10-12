import asyncio
from pathlib import Path
from typing import Any

from nyx.types import Tool

DEFAULT_WRITE_ROOT = Path("workspace")
_COMPARE_CHUNK_SIZE = 64 * 1024


def is_internal(path: Path, blocked: list[Path] | tuple[Path, ...]) -> bool:
    """Resolve aliases before comparing internal save boundaries."""
    resolved = path.resolve()
    return any(
        resolved == root.resolve() or resolved.is_relative_to(root.resolve())
        for root in blocked
    )


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


def _matches_existing_bytes(target: Path, payload: bytes) -> bool:
    offset = 0
    with target.open("rb") as existing:
        while chunk := existing.read(_COMPARE_CHUNK_SIZE):
            end = offset + len(chunk)
            if end > len(payload) or chunk != payload[offset:end]:
                return False
            offset = end
    return offset == len(payload)


async def file_io(
    action: str,
    path: str,
    content: str | None = None,
    write_root: Path = DEFAULT_WRITE_ROOT,
    *,
    blocked: list[Path] | tuple[Path, ...] = (),
) -> dict[str, Any]:
    """read 全盘读 / write 写进 write_root / list 列目录（全盘）。

    已知边界：read/list 全盘是有意设计（探索特性），本地单机 agent 以用户权限
    运行、非沙箱；LLM 可经 exploration focus 指向任意路径，MVP 接受，不提供
    对外服务隔离。write 仍受 write_root 越界校验约束；同路径同内容不重复写盘。
    """
    if action in ("read", "list") and is_internal(Path(path), blocked):
        raise ValueError("不能读取其他角色或宿主的内部数据")
    if action == "read":
        text = await asyncio.to_thread(
            Path(path).read_text, encoding="utf-8", errors="replace"
        )
        return {"path": path, "content": text}
    if action == "write":
        target = _resolve_write(write_root, path)
        payload = content or ""

        def _do() -> int:
            target.parent.mkdir(parents=True, exist_ok=True)
            payload_bytes = payload.encode("utf-8")
            if target.is_file() and _matches_existing_bytes(target, payload_bytes):
                return len(payload)
            return target.write_text(payload, encoding="utf-8")

        worker = asyncio.get_running_loop().run_in_executor(None, _do)
        cancelled = False
        while True:
            try:
                written = await asyncio.shield(worker)
                break
            except asyncio.CancelledError:
                # A thread cannot be cancelled. Keep its owning runner alive until
                # disk I/O ends so unloading cannot reopen the same workspace early.
                cancelled = True
                if worker.cancelled():
                    raise
        if cancelled:
            raise asyncio.CancelledError
        return {"path": str(target), "written": written}
    if action == "list":
        return {
            "path": path,
            "entries": await asyncio.to_thread(_list_entries, path, blocked),
        }
    raise ValueError(f"未知 file_io 动作：{action!r}（应为 read/write/list）")


def _list_entries(path: str, blocked: list[Path] | tuple[Path, ...] = ()) -> list[str]:
    return [p.name for p in Path(path).iterdir() if not is_internal(p, blocked)]


def build_file_io_tool(
    write_root: Path = DEFAULT_WRITE_ROOT,
    *,
    blocked: list[Path] | tuple[Path, ...] = (),
) -> Tool:
    async def handler(
        action: str, path: str, content: str | None = None
    ) -> dict[str, Any]:
        return await file_io(action, path, content, write_root, blocked=blocked)

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
