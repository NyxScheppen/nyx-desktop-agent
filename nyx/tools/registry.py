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
        # OpenAI 兼容 function calling 格式：{"type":"function","function":{…}}。
        # complete() 把 tools 原样透传（不二次包装），故这里须带 type/function 外壳。
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
