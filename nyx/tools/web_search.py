# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false
# lxml 无类型 stub，html.fromstring / Element.xpath 推成 Unknown，与本项目对
# 无类型第三方库的处理一致（探索模块同款豁免）。
"""联网搜索：Bing（cn.bing.com，大陆可达），返回 [{title, url, snippet}]。

原 ddgs 多引擎聚合在大陆网络下全被墙（Brave/DDG/Yandex DNS 封锁；bing/google
引擎在 ddgs 9.15.0 里 disabled），一次搜索慢失败 42s 且静默返回空。改用 httpx
直抓 Bing 结果页，保留 web_search 工具契约不变。
"""
import asyncio
import logging
from typing import cast

import httpx
from lxml import html

from nyx.types import Tool

_logger = logging.getLogger(__name__)

_BING_URL = "https://cn.bing.com/search"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
_MAX_RESULTS = 5


def _parse_bing(html_text: str) -> list[dict[str, str]]:
    """从 Bing 结果页提取 [{title, url, snippet}]（纯函数，便于单测）。

    Bing 结果项是 li.b_algo：标题/链接在 h2/a，摘要在 p。
    """
    tree = html.fromstring(html_text)
    results: list[dict[str, str]] = []
    for li in tree.xpath("//li[contains(@class, 'b_algo')]"):
        title = "".join(cast("list[str]", li.xpath(".//h2/a//text()"))).strip()
        hrefs = cast("list[str]", li.xpath(".//h2/a/@href"))
        snippet = "".join(cast("list[str]", li.xpath(".//p//text()"))).strip()
        url = hrefs[0] if hrefs else ""
        if not title or not url:
            continue
        results.append({"title": title, "url": url, "snippet": snippet})
    return results


def _search_web_sync(query: str) -> list[dict[str, str]]:
    """联网搜索主路径：GET cn.bing.com → 解析；失败/无结果记日志返回空。"""
    try:
        resp = httpx.get(
            _BING_URL,
            params={"q": query, "setlang": "zh-cn"},
            headers=_HEADERS,
            timeout=10.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception:
        # best-effort：联网失败/超时返回空，不崩回复（use_tools 层另有兜底）
        _logger.warning("联网搜索请求失败 query=%s", query, exc_info=True)
        return []
    results = _parse_bing(resp.text)[:_MAX_RESULTS]
    if not results:
        _logger.warning("联网搜索无结果 query=%s", query)
    return results


def build_web_search_tool() -> Tool:
    async def handler(query: str) -> list[dict[str, str]]:
        return await asyncio.to_thread(_search_web_sync, query)

    return Tool(
        name="web_search",
        description="联网搜索（Bing），返回标题 / 链接 / 摘要。",
        schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索关键词"}},
            "required": ["query"],
        },
        handler=handler,
    )
