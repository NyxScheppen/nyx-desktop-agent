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
