# 工具系统

> 范围：`tools/registry.py`（`ToolRegistry`：register / call / schema）、`tools/local_search.py`、`tools/web_search.py`、`tools/file_io.py`、`tools/web_fetch.py`（四个内置工具）。
> 纯基础设施 spec：只做「工具注册 + 工具执行 + 四个内置工具」，不含 Facade、不含 API、不含 LangGraph 工具绑定（那是 14-activity 与 17-expression 的活）。
> **本文件自包含**：5 个文件的完整代码内联在下文。

## 元信息

- **前置依赖**：01-types（`Tool` dataclass：`name` / `description` / `schema` / `handler`）

## 用户故事

> 作为 Nyx 系统的开发者，我想要一套工具注册/执行机制 + 四个内置工具（本地搜索、联网搜索、文件读写、网页抓取），以便活动模块通过 `ToolRegistry` 调用工具、LLM 通过 `schema()` 得知可用工具、所有工具 I/O 可 mock 可测。

## 验收标准

- [ ] `registry.py` 含 `ToolRegistry`（`register` / `call` / `schema`），与「`tools/registry.py`（完整）」段代码逐字一致
- [ ] 三个工具模块各含 `build_*_tool()` 工厂 + 对应 handler，与各自「（完整）」段代码逐字一致
- [ ] `register` 重名 → `ValueError`；`call` 用 `handler(**args)` 调 handler；`call` 未注册名 → `KeyError`
- [ ] `schema()` 返回 `[{"type":"function","function":{name, description, parameters}}]`，按注册序
- [ ] `file_io` 的 `write` 越界（`../` 或绝对路径逃逸 `write_root`）→ `ValueError`；`read` / `list` 不受写目录限制
- [ ] `local_search` 缺省搜全盘（`full_disk_roots()`），`roots` 参数可收窄；遍历跳过无权限目录不崩
- [ ] 三个工具返回 JSON 可序列化数据（`dict` / `list` / `str`，不返回 domain dataclass）
- [ ] `web_search` 的 opt-in 由组合根（18-api）按 `config.exploration.web_enabled` 决定是否注册；06-tools 本身不读 config
- [ ] `web_fetch` 抓网页正文（httpx GET + trafilatura 抽正文）→ 写 `uploads/<safe>.txt`（复用 `file_io` write）→ 发布 `USER_MATERIAL`（复用 `_on_user_material` 注册 + 触发读书）；抓取失败/空正文返回 `{"error": ...}`（best-effort 不崩）
- [ ] `pyright` strict 零报错

## 技术方案

- **新文件**：`nyx/tools/registry.py`、`nyx/tools/local_search.py`、`nyx/tools/web_search.py`、`nyx/tools/file_io.py`、`nyx/tools/web_fetch.py`（无 Facade、无 API、无数据变更）
- **库**：`ddgs`（原 `duckduckgo_search` 已改名，`DDGS` 无 key 搜索）。**版本敏感契约以锁定版本为准**：`DDGS` 支持 `with` 上下文管理器、构造参数 `timeout`（超时，秒）、`text(query, max_results=5)` 的参数名 `max_results`、返回字段 `title` / `href` / `body`——升级依赖须重跑本 spec 测试（同 03-llm 的 usage_metadata 锁定约定）；其余为标准库（`asyncio` / `pathlib` / `os` / `string`）
- **公开面**：`from nyx.tools.registry import ToolRegistry`；`from nyx.tools.local_search import build_local_search_tool, search_local`；`from nyx.tools.web_search import build_web_search_tool`；`from nyx.tools.file_io import build_file_io_tool, file_io`；`from nyx.tools.web_fetch import build_web_fetch_tool, fetch_url`（不加 `__all__`）
- **handler 调用约定**：`call(name, args)` 用 `await handler(**args)` 把 `args` dict 解包为关键字实参；`Tool.schema` 的键 = handler 形参名（如 `{query}` → `handler(query=...)`）。参数不匹配时 `TypeError` 上抛（handler 签名兜底，不额外校验 schema）
- **不校验 args 与 schema**：不引入 JSON schema validator（新依赖 + 复杂度）；schema 是给 LLM 的契约，handler 签名是给运行时的契约
- **`Tool.schema` vs `schema()`**：`Tool.schema` 存**参数** JSON schema（`{"type":"object","properties":…,"required":[…]}`）；`ToolRegistry.schema()` 把它包成 OpenAI 兼容 function calling 格式 `[{"type":"function","function":{name, description, parameters}}]`，按注册序输出（`LlmClient.complete` 原样透传给 API，不二次包装）
- **重复注册 → `ValueError`**：fail-fast 抓组合根的布线 bug（重复注册同名工具）；`register` 不静默覆盖
- **结果 JSON 可序列化**：工具返回 `dict` / `list` / `str`，不返回 domain dataclass——结果要进 14-activity / 17-expression 的 LLM 上下文
- **全 async + 不阻塞事件循环**：fs / network 是阻塞 I/O，用 `asyncio.to_thread` 包一层（CLAUDE.md「I/O 操作用 async def」+ 不卡 SSE 广播）
- **`web_search` opt-in 归组合根**：06-tools 只提供 `build_web_search_tool()`；`main.py`（18-api）读 `config.exploration.web_enabled`，true 才注册。未注册 → 不出现在 `schema()` 里，LLM 不可见、`call` 报 `KeyError`
- **`web_fetch` 抓正文入书库（主动下载资料）**：`fetch_url(url)`（httpx GET + trafilatura 抽正文，失败/空返 `""`，`asyncio.to_thread` 不阻塞事件循环）→ `build_web_fetch_tool(bus)` 写 `uploads/<safe>.txt`（复用 `file_io`）→ `bus.publish(USER_MATERIAL, {path, filename, total_chars})` 复用 `_on_user_material` 的「注册 + 触发读书」链路。正文超 `_MAX_DOWNLOAD_CHARS`（20 万）截断；文件名由 URL 派生（`re.sub` 清洗非词符），不复刻 `activity/facade._sanitize_filename`（避免 tools→activity 反向依赖）。依赖新增 `trafilatura`（pyproject）
- **`file_io` 沙箱（只读 + 指定写目录）**：`read` / `list` 全盘（读安全，agent 需要读任意书/文件）；`write` 限定 `write_root`（默认 `Path("workspace")`，相对 cwd），越界抛 `ValueError`。路径校验用 `pathlib` 的 `.resolve()` + `.is_relative_to()`。已知边界：`read`/`list` 全盘是有意设计（探索特性），本地单机 agent 以用户权限运行、非沙箱，LLM 可经 exploration `focus` 指向任意路径——MVP 接受，不提供对外服务隔离（不为此加 read_root 配置）
- **`local_search` 范围**：缺省搜**全盘**（`full_disk_roots()`：Windows 枚举存在的盘符、POSIX 根 `/`），与 `file_io.read` 的「读可全盘」一致；`.txt` / `.md` 文本，大小写不敏感子串匹配，返回 `[{path, snippet}]`。`roots` 参数可收窄（探索链传 `[workspace]`、测试传 `[tmp_path]`）。与记忆检索（08-memory-retrieval）是两码事——本工具搜**文件**，不搜记忆表
- **全盘遍历用 `os.walk` + `onerror` 跳过无权限目录**：`rglob` 在无权限目录（Windows `System Volume Information` 等）会抛 `PermissionError`；`os.walk(root, onerror=...)` 跳过不可读目录继续走。注意全盘搜索慢（冷跑可能分钟级），探索链若要收窄用 `roots` 参数；结果截断到 `_MAX_RESULTS`（50）、单文件超 `_MAX_FILE_BYTES`（1MiB）跳过（界内存/耗时兜底，不做超时——`to_thread` 无法干净中断 os.walk 线程）
- **注入非全局**：`ToolRegistry` 是普通类，组合根实例化 + 注入活动 Facade 与表达 Facade（同 EventBus 约定），无模块级单例

### `tools/registry.py`（完整）

```python
from typing import Any

from nyx.types import Tool


class ToolRegistry:
    """工具注册表：register 注册、call 执行、schema 出 LLM 工具定义。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具名重复注册：{tool.name!r}")
        self._tools[tool.name] = tool

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"未注册工具：{name!r}")
        return await tool.handler(**args)

    def schema(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.schema,
                },
            }
            for t in self._tools.values()
        ]
```

### `tools/local_search.py`（完整）

```python
import asyncio
import os
import string
from pathlib import Path

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


def _search_local_sync(query: str, roots: list[Path]) -> list[dict[str, str]]:
    """同步核心：os.walk 遍历，onerror 跳过无权限目录，.txt/.md 大小写不敏感子串。

    结果截断到 _MAX_RESULTS、单文件超 _MAX_FILE_BYTES 跳过（全盘扫描兜底）。
    """
    results: list[dict[str, str]] = []
    needle = query.lower()
    for root in roots:
        for dirpath, _dirnames, filenames in os.walk(root, onerror=lambda _e: None):
            for name in filenames:
                if len(results) >= _MAX_RESULTS:
                    return results
                if Path(name).suffix.lower() not in _SEARCH_SUFFIXES:
                    continue
                full = os.path.join(dirpath, name)
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
```

### `tools/web_search.py`（完整）

```python
import asyncio

from ddgs import DDGS

from nyx.types import Tool


def _search_web_sync(query: str) -> list[dict[str, str]]:
    try:
        with DDGS(timeout=10) as ddgs:
            raw = ddgs.text(query, max_results=5)
    except Exception:
        # best-effort：联网失败/超时返回空，不崩回复（use_tools 层另有兜底）
        return []
    return [{"title": r["title"], "url": r["href"], "snippet": r["body"]} for r in raw]


def build_web_search_tool() -> Tool:
    async def handler(query: str) -> list[dict[str, str]]:
        return await asyncio.to_thread(_search_web_sync, query)

    return Tool(
        name="web_search",
        description="联网搜索（DuckDuckGo），返回标题 / 链接 / 摘要。",
        schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索关键词"}},
            "required": ["query"],
        },
        handler=handler,
    )
```

### `tools/file_io.py`（完整）

```python
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
    """read 全盘读 / write 写进 write_root / list 列目录（全盘）。

    已知边界：read/list 全盘是有意设计（探索特性），本地单机 agent 以用户权限
    运行、非沙箱；LLM 可经 exploration focus 指向任意路径，MVP 接受，不提供
    对外服务隔离。write 仍受 write_root 越界校验约束。
    """
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
```

### `tools/web_fetch.py`（完整）

```python
import asyncio
import re
import time
from typing import Any
from uuid import uuid4

import httpx
import trafilatura

from nyx.enums import EventType, Source
from nyx.events.bus import EventBus
from nyx.tools.file_io import file_io
from nyx.types import Event, Tool

_MAX_DOWNLOAD_CHARS = 200_000  # 单篇下载正文字符上限（decision，可推翻）
_FILENAME_MAX_LEN = 80         # 派生文件名截断长度


def _filename_from_url(url: str) -> str:
    """从 URL 派生安全文件名（去 scheme、非词符换 _）。

    不复刻 activity/facade._sanitize_filename（避免 tools→activity 反向依赖）。
    """
    stripped = url.split("://", 1)[-1] if "://" in url else url
    name = re.sub(r"[^\w\-.]", "_", stripped)
    return name[:_FILENAME_MAX_LEN] or "downloaded"


def _fetch_url_sync(url: str) -> str:
    """同步抓正文（httpx GET + trafilatura 抽正文）。失败/无正文返回 ""。"""
    try:
        resp = httpx.get(url, timeout=15.0, follow_redirects=True)
        resp.raise_for_status()
        text = trafilatura.extract(resp.text)
    except Exception:
        # best-effort：联网失败/超时/解析失败返回空串，不崩调用方
        return ""
    return text or ""


async def fetch_url(url: str) -> str:
    """异步抓正文，I/O 走线程池。"""
    return await asyncio.to_thread(_fetch_url_sync, url)


def build_web_fetch_tool(bus: EventBus) -> Tool:
    """抓取网页正文 → 写进 uploads/ → 发布 USER_MATERIAL 入书库（复用 _on_user_material
    的「注册 + 触发读书」链路）。"""

    async def handler(url: str) -> dict[str, Any]:
        text = await fetch_url(url)
        if not text.strip():
            return {"error": "正文抓取失败或为空"}
        if len(text) > _MAX_DOWNLOAD_CHARS:
            text = text[:_MAX_DOWNLOAD_CHARS]
        name = _filename_from_url(url)
        filename = f"{name}.txt"
        written = await file_io("write", f"uploads/{filename}", text)
        path = str(written["path"])
        cid = str(uuid4())
        await bus.publish(
            Event(
                id=cid,
                timestamp=time.time(),
                source=Source.INTERNAL,
                type=EventType.USER_MATERIAL,
                content={
                    "path": path,
                    "filename": filename,
                    "total_chars": len(text),
                },
                correlation_id=cid,
            )
        )
        return {"path": path, "filename": filename, "total_chars": len(text)}

    return Tool(
        name="web_fetch",
        description="抓取网页正文为纯文本，写进书库供后续阅读（下载资料来读）。",
        schema={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "网页 URL"}},
            "required": ["url"],
        },
        handler=handler,
    )
```

## 测试要点

- [ ] 单元测试 `tests/test_tools/`：
  - [ ] **registry**（`test_registry.py`，fake `Tool`）：
    - [ ] `register` + `schema()` 按注册序返回 `[{"type":"function","function":{name, description, parameters}}]`
    - [ ] 重复 `register` 同名 → `ValueError`
    - [ ] `call` 用 `handler(**args)` 调 handler（fake handler 记录收到的 kwargs 与返回值）
    - [ ] `call` 未注册名 → `KeyError`
  - [ ] **local_search**（`test_local_search.py`，`roots=[tmp_path]` 收窄；全盘默认不真测以免扫真盘）：
    - [ ] 命中：`tmp_path/a.md` 含关键词 → 返回 `[{path, snippet}]`，`path` 是 `a.md`
    - [ ] 不命中 → `[]`；`roots=[]` 或根不存在 → `[]`
    - [ ] 大小写不敏感（"DEEP" 命中 "deep sea"）
    - [ ] 非 `.txt`/`.md` 文件跳过
    - [ ] 命中超 `_MAX_RESULTS` → 截断到 50；单文件超 `_MAX_FILE_BYTES` → 跳过
    - [ ] `full_disk_roots()`：非空且每项 `.exists()`；POSIX 下 `== [Path("/")]`
  - [ ] **web_search**（`test_web_search.py`，monkeypatch `DDGS` 为 fake，返回预设结果）：
    - [ ] `build_web_search_tool().handler("x")` → `[{title, url, snippet}]`（fake 的字段映射正确）
    - [ ] fake 抛异常 → 返回 `[]`（best-effort 不冒泡）
    - [ ] 不触真实网络
  - [ ] **file_io**（`test_file_io.py`，`tmp_path` 作 `write_root`）：
    - [ ] `read`：写一个 tmp 文件 → `file_io("read", path)` 返回 `content`
    - [ ] `write`：`file_io("write", "note.txt", "hi", write_root)` → 文件建在 `write_root/note.txt`，返回 `written`
    - [ ] `write` 越界：`file_io("write", "../evil.txt", ..., write_root)` → `ValueError`
    - [ ] `write` 绝对路径逃逸 → `ValueError`
    - [ ] `list`：返回目录条目名
    - [ ] 未知 `action` → `ValueError`
  - [ ] **web_fetch**（`test_web_fetch.py`，monkeypatch `fetch_url`/`file_io` + fake bus）：
    - [ ] `_fetch_url_sync`：monkeypatch `httpx.get` 抛异常 → 返回 `""`（best-effort 不冒泡）
    - [ ] `build_web_fetch_tool().handler(url)`：写盘（`file_io("write", ...)`）+ 发布 `USER_MATERIAL`（fake bus 捕获事件，断言 `type is USER_MATERIAL`/`source is INTERNAL`/`content` 键 `{path, filename, total_chars}`）
    - [ ] 抓取空正文 → 返回 `{"error": ...}` 且不 publish
    - [ ] 不触真实网络
- [ ] 集成测试：无（工具是基础设施，无 Facade 管道；真实绑定归 18-api 组合根）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 活动 Facade 调工具都经 `ToolRegistry`（无绕过）；`schema()` 产物为 OpenAI 兼容 function calling 格式（`LlmClient.complete` 原样透传 `tools` 给 API）；`file_io.write` 永不越出 `write_root`
