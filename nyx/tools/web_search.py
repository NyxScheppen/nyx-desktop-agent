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
from xml.etree import ElementTree

import httpx
from lxml import html

from nyx.policy import policy
from nyx.types import Tool

_logger = logging.getLogger(__name__)

_BING_URL = policy("tools", "web_search._BING_URL", str)
_HEADERS = policy("tools", "web_search._HEADERS", dict[str, str])
_MAX_RESULTS = policy("tools", "web_search._MAX_RESULTS", int)


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
    """优先解析 Bing 页面，无结果时读取 RSS；失败如实返回空。"""
    try:
        resp = httpx.get(
            _BING_URL,
            params={"q": query, "setlang": "zh-cn"},
            headers=_HEADERS,
            timeout=policy(
                "tools", "web_search.parameters.search_web_sync_timeout_factor", float
            ),
            follow_redirects=True,
        )
        resp.raise_for_status()
        results = _parse_bing(resp.text)
        if not results:
            # 云端可能收到无结果的 HTML；同一搜索服务的 RSS 不依赖页面结构。
            feed = httpx.get(
                _BING_URL,
                params={"q": query, "format": "rss"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=policy(
                    "tools",
                    "web_search.parameters.search_web_sync_timeout_factor_2",
                    float,
                ),
                follow_redirects=True,
            )
            feed.raise_for_status()
            root = ElementTree.fromstring(feed.text)
            results = [
                {"title": title, "url": url,
                 "snippet": item.findtext("description", "")}
                for item in root.findall("./channel/item")
                if (title := item.findtext("title", "").strip())
                and (url := item.findtext("link", "").strip())
            ]
    except Exception:
        # best-effort：联网失败/超时返回空，不崩回复（use_tools 层另有兜底）
        _logger.warning(
            policy("tools", "web_search.copy.search_web_sync_text_2", str),
            query,
            exc_info=True,
        )
        return []
    results = results[:_MAX_RESULTS]
    if not results:
        _logger.warning(
            policy("tools", "web_search.copy.search_web_sync_text", str), query
        )
    return results


def build_web_search_tool() -> Tool:
    async def handler(query: str) -> list[dict[str, str]]:
        return await asyncio.to_thread(_search_web_sync, query)

    return Tool(
        name="web_search",
        description=policy("tools", "web_search.copy.build_web_search_tool_text", str),
        schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": policy(
                        "tools", "web_search.copy.build_web_search_tool_text_2", str
                    ),
                }
            },
            "required": ["query"],
        },
        handler=handler,
    )
