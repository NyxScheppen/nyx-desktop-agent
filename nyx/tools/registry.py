import json
import logging
import time
from collections.abc import Iterable
from typing import Any
from uuid import uuid4

from nyx.tools.tool_call_store import ToolCallStore
from nyx.types import Tool

_LOGGER = logging.getLogger(__name__)
_ARGS_MAX_CHARS = 2000
_RESULT_MAX_CHARS = 2000
_ERROR_MAX_CHARS = 1000


def is_read_only_call(name: str, args: dict[str, Any]) -> bool:
    """Only known reads may repeat after an unconfirmed attempt."""
    return name in (
        "local_search", "web_search", "memory_recall", "knowledge_search",
    ) or (
        name == "file_io" and args.get("action") in ("read", "list")
    )


class ToolRegistry:
    """工具注册表：register 注册、call 执行、schema 出 LLM 工具定义。"""

    def __init__(self, store: ToolCallStore | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._store = store
        self._executions: dict[str, dict[str, Any]] = {}

    async def start_execution(
        self, log_id: str, correlation_id: str,
    ) -> dict[str, Any] | None:
        """Claim execution durably, or locally when using an in-memory graph."""
        if self._store is not None:
            return await self._store.start_execution(log_id, correlation_id)
        existing = self._executions.get(log_id)
        if existing is None:
            self._executions[log_id] = {
                "correlation_id": correlation_id, "status": "running",
                "result_json": None, "error": None,
            }
        return existing

    async def get_execution(self, log_id: str) -> dict[str, Any] | None:
        """Read a receipt while replacing input, without starting an old action."""
        if self._store is not None:
            return await self._store.get_execution(log_id)
        return self._executions.get(log_id)

    async def finish_execution(
        self, log_id: str, result: Any, error: str | None,
    ) -> None:
        """Receipt errors propagate; this is not best-effort observation."""
        if self._store is not None:
            await self._store.finish_execution(log_id, result, error)
        else:
            self._executions[log_id].update(
                status="success" if error is None else "error",
                result_json=(
                    json.dumps(result, ensure_ascii=False) if error is None else None
                ),
                error=error,
            )

    async def clear_executions(self, correlation_id: str) -> None:
        """Release completed reply receipts along with its checkpoint."""
        if self._store is not None:
            await self._store.clear_executions(correlation_id)
        else:
            self._executions = {
                key: value for key, value in self._executions.items()
                if value["correlation_id"] != correlation_id
            }

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具名重复注册：{tool.name!r}")
        self._tools[tool.name] = tool

    async def call(
        self,
        name: str,
        args: dict[str, Any],
        *,
        correlation_id: str | None = None,
        call_id: str | None = None,
        log_id: str | None = None,
    ) -> Any:
        """Keep the handler result/exception intact; observation is best-effort."""
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"未注册工具：{name!r}")
        if self._store is None:
            return await tool.handler(**args)
        created_at = time.time()
        started = time.perf_counter()
        result: Any = None
        error: str | None = None
        status = "error"
        try:
            result = await tool.handler(**args)
            status = "success"
            return result
        except BaseException as exc:
            error = str(exc)[:_ERROR_MAX_CHARS]
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            try:
                await self._store.insert({
                    "id": log_id if log_id is not None else str(uuid4()),
                    "created_at": created_at,
                    "correlation_id": correlation_id,
                    "call_id": call_id,
                    "tool_name": name,
                    "args_json": json.dumps(
                        args, ensure_ascii=False, sort_keys=True
                    )[:_ARGS_MAX_CHARS],
                    "status": status,
                    "duration_ms": duration_ms,
                    "result_json": (
                        json.dumps(result, ensure_ascii=False)[:_RESULT_MAX_CHARS]
                        if status == "success" else None
                    ),
                    "error": error,
                })
            except Exception:
                _LOGGER.exception("工具观测落库失败 tool=%s", name)

    async def mark_adopted(self, log_id: str) -> None:
        """Observation failure must not change the caller's accepted result."""
        if self._store is not None:
            try:
                await self._store.mark_adopted(log_id)
            except Exception:
                _LOGGER.exception("工具采纳标记失败 log_id=%s", log_id)

    def schema(self, names: Iterable[str] | None = None) -> list[dict[str, Any]]:
        # OpenAI 兼容 function calling 格式：{"type":"function","function":{…}}。
        # complete() 把 tools 原样透传（不二次包装），故这里须带 type/function 外壳。
        selected = (
            self._tools.values()
            if names is None else [self._tools[name] for name in names]
        )
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.schema,
                },
            }
            for t in selected
        ]
