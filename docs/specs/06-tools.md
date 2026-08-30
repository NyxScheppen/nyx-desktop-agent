# 工具系统

> 范围：`tools/registry.py`（`ToolRegistry`：register / call / schema）、`tools/local_search.py`、`tools/web_search.py`、`tools/file_io.py`、`tools/web_fetch.py`（四个内置工具）。
> 纯基础设施 spec：只做「工具注册 + 工具执行 + 四个内置工具」，不含 Facade、不含 API、不含 LangGraph 工具绑定（那是 14-activity 与 17-expression 的活）。
> spec 只定义契约（签名 + 语义 + 决策）；实现以 `nyx/tools/registry.py` / `nyx/tools/local_search.py` / `nyx/tools/web_search.py` / `nyx/tools/file_io.py` / `nyx/tools/web_fetch.py` 源文件为准。

## 元信息

- **前置依赖**：01-types（`Tool` dataclass：`name` / `description` / `schema` / `handler`）

## 用户故事

> 作为 Nyx 系统的开发者，我想要一套工具注册/执行机制 + 四个内置工具（本地搜索、联网搜索、文件读写、网页抓取），以便活动模块通过 `ToolRegistry` 调用工具、LLM 通过 `schema()` 得知可用工具、所有工具 I/O 可 mock 可测。

## 验收标准

- [ ] `registry.py` 含 `ToolRegistry`（`register` / `call` / `schema`）（实现见 `nyx/tools/registry.py`）
- [ ] 四个工具模块各含 `build_*_tool()` 工厂 + 对应 handler（实现见 `nyx/tools/local_search.py` / `web_search.py` / `file_io.py` / `web_fetch.py`）
- [ ] `register` 重名 → `ValueError`；`call` 用 `handler(**args)` 调 handler；`call` 未注册名 → `KeyError`
- [ ] `schema()` 返回 `[{"type":"function","function":{name, description, parameters}}]`，按注册序
- [ ] `file_io` 的 `write` 越界（`../` 或绝对路径逃逸 `write_root`）→ `ValueError`；`read` / `list` 不受写目录限制
- [ ] `local_search` 缺省搜全盘（`full_disk_roots()`），`roots` 参数可收窄；遍历跳过无权限目录不崩
- [ ] 四个工具返回 JSON 可序列化数据（`dict` / `list` / `str`，不返回 domain dataclass）
- [ ] `web_search` 的 opt-in 由组合根（18-api）按 `config.exploration.web_enabled` 决定是否注册；06-tools 本身不读 config
- [ ] `web_fetch` 抓网页正文（httpx GET + trafilatura 抽正文）→ 返回 `{"text", "url"}` 纯正文（不写盘、不发事件，供探索直接消化）；抓取失败/空正文返回 `{"error": ...}`（best-effort 不崩）
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
- **`web_fetch` 抓正文（纯抓取，不落盘不触发读书）**：`fetch_url(url)`（httpx GET + trafilatura 抽正文，失败/空返 `""`，`asyncio.to_thread` 不阻塞事件循环）→ `build_web_fetch_tool()` 返回 `{"text", "url"}` 纯正文（正文超 `_MAX_DOWNLOAD_CHARS`（20 万）截断），供 14-activity 探索直接消化。不再写 `uploads/`、不再发 `USER_MATERIAL`（读书由欲望驱动从书库选书，见 14-activity）。依赖新增 `trafilatura`（pyproject）
- **`file_io` 沙箱（只读 + 指定写目录）**：`read` / `list` 全盘（读安全，agent 需要读任意书/文件）；`write` 限定 `write_root`（默认 `Path("workspace")`，相对 cwd），越界抛 `ValueError`。路径校验用 `pathlib` 的 `.resolve()` + `.is_relative_to()`。已知边界：`read`/`list` 全盘是有意设计（探索特性），本地单机 agent 以用户权限运行、非沙箱，LLM 可经 exploration `focus` 指向任意路径——MVP 接受，不提供对外服务隔离（不为此加 read_root 配置）
- **`local_search` 范围**：缺省搜**全盘**（`full_disk_roots()`：Windows 枚举存在的盘符、POSIX 根 `/`），与 `file_io.read` 的「读可全盘」一致；`.txt` / `.md` 文本，大小写不敏感子串匹配，返回 `[{path, snippet}]`。`roots` 参数可收窄（探索链传 `[workspace]`、测试传 `[tmp_path]`）。与记忆检索（08-memory-retrieval）是两码事——本工具搜**文件**，不搜记忆表
- **全盘遍历用 `os.walk` + `onerror` 跳过无权限目录**：`rglob` 在无权限目录（Windows `System Volume Information` 等）会抛 `PermissionError`；`os.walk(root, onerror=...)` 跳过不可读目录继续走。注意全盘搜索慢（冷跑可能分钟级），探索链若要收窄用 `roots` 参数；结果截断到 `_MAX_RESULTS`（50）、单文件超 `_MAX_FILE_BYTES`（1MiB）跳过（界内存/耗时兜底，不做超时——`to_thread` 无法干净中断 os.walk 线程）
- **注入非全局**：`ToolRegistry` 是普通类，组合根实例化 + 注入活动 Facade 与表达 Facade（同 EventBus 约定），无模块级单例

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
    - [ ] `build_web_fetch_tool().handler(url)`：返回 `{"text": "正文内容", "url": ...}` 纯正文（不写盘、不发事件）
    - [ ] 抓取空正文 → 返回 `{"error": ...}`
    - [ ] 不触真实网络
- [ ] 集成测试：无（工具是基础设施，无 Facade 管道；真实绑定归 18-api 组合根）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 活动 Facade 调工具都经 `ToolRegistry`（无绕过）；`schema()` 产物为 OpenAI 兼容 function calling 格式（`LlmClient.complete` 原样透传 `tools` 给 API）；`file_io.write` 永不越出 `write_root`
