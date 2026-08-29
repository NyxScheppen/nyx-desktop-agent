import asyncio
import ipaddress
import re
import socket
import time
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx
import trafilatura

from nyx.enums import EventType, Source
from nyx.events.bus import EventBus
from nyx.tools.file_io import file_io
from nyx.types import Event, Tool

_MAX_DOWNLOAD_CHARS = 200_000  # 单篇下载正文字符上限（decision，可推翻）
_FILENAME_MAX_LEN = 80         # 派生文件名截断长度
_MAX_REDIRECTS = 5             # 重定向跳数上限（逐跳做 SSRF 校验，防无限循环）


def _filename_from_url(url: str) -> str:
    """从 URL 派生安全文件名（去 scheme、非词符换 _）。

    不复刻 activity/facade._sanitize_filename（避免 tools→activity 反向依赖）。
    """
    stripped = url.split("://", 1)[-1] if "://" in url else url
    name = re.sub(r"[^\w\-.]", "_", stripped)
    return name[:_FILENAME_MAX_LEN] or "downloaded"


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """SSRF 护栏：拒绝本机/内网/链路本地/保留/组播/未指定地址。"""
    return not (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _is_safe_url(url: str) -> bool:
    """SSRF 护栏：仅允许公网 http(s)，主机名解析出的每个 IP 都必须是公网地址。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if not _is_public_ip(ip):
            return False
    return True


def _fetch_url_sync(url: str) -> str:
    """同步抓正文（httpx GET + trafilatura 抽正文）。失败/无正文返回 ""。

    SSRF 防护：不自动跟随重定向，逐跳用 _is_safe_url 校验目标（公网 http(s) 才放行）。
    """
    for _ in range(_MAX_REDIRECTS + 1):
        if not _is_safe_url(url):
            return ""
        try:
            resp = httpx.get(url, timeout=15.0, follow_redirects=False)
        except Exception:
            return ""
        if resp.is_redirect:
            location = resp.headers.get("location")
            if not location:
                return ""
            url = urljoin(url, location)
            continue
        try:
            resp.raise_for_status()
            text = trafilatura.extract(resp.text)
        except Exception:
            return ""
        return text or ""
    return ""


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
