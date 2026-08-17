import asyncio

from duckduckgo_search import DDGS

from nyx.types import Tool


def _search_web(query: str) -> list[dict[str, str]]:
    with DDGS() as ddgs:
        raw = ddgs.text(query, max_results=5)
    return [{"title": r["title"], "url": r["href"], "snippet": r["body"]} for r in raw]


def build_web_search_tool() -> Tool:
    async def handler(query: str) -> list[dict[str, str]]:
        return await asyncio.to_thread(_search_web, query)

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
