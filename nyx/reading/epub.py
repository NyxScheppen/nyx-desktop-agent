"""EPUB 解析：字节 → 元数据 + 段落 + 内容哈希（reading-system spec）。同步、无 LLM。

ebooklib 无类型标注，本文件对它的返回值按 untyped 第三方处理（见文件头
pyright 豁免）。title/author 缺失回退空串——title 的 filename 回退由
`ReadingFacade.import_book` 负责（`parse_epub` 只拿 bytes、不知文件名）。
"""

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
import hashlib
import io
import posixpath
import zipfile
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urldefrag
from xml.etree import ElementTree

from ebooklib import ITEM_DOCUMENT, epub

from nyx.policy import policy
from nyx.reading.segmenter import Segment, segment_html


@dataclass
class EpubResult:
    """EPUB 解析结果。title/author 缺失时为空串。"""

    title: str
    author: str
    segments: list[Segment]
    content_hash: str


_MAX_UNCOMPRESSED_BYTES = policy(
    "reading", "epub._MAX_UNCOMPRESSED_BYTES", int
)  # decision：解压后总大小上限（防 zip 炸弹）


def _check_uncompressed_size(data: bytes) -> None:
    """预检解压后总大小（读中央目录，不解压）；超限抛 `ValueError`。"""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        total = sum(info.file_size for info in zf.infolist())
    if total > _MAX_UNCOMPRESSED_BYTES:
        raise ValueError(
            policy(
                "reading", "epub.templates.check_uncompressed_size_text", str
            ).format(p0=total, p1=_MAX_UNCOMPRESSED_BYTES)
        )


def _toc_sections(items: list[Any]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}

    def visit(entries: list[Any], parents: list[str]) -> None:
        for entry in entries:
            node = entry[0] if isinstance(entry, tuple) else entry
            title = str(node.title).strip()
            path, anchor = urldefrag(unquote(node.href))
            titles = [*parents, title] if title else parents
            if path and title:
                result.setdefault(posixpath.normpath(path), {})[anchor] = " / ".join(
                    titles
                )
            if isinstance(entry, tuple):
                visit(entry[1], titles)

    visit(items, [])
    return result


def parse_epub(data: bytes) -> EpubResult:
    """EPUB 字节 → 元数据 + 正文段落 + 全文 SHA-256。

    按 spine 阅读序遍历，跳过 `linear=='no'` 与非 `ITEM_DOCUMENT`（图片/CSS）。
    非 ZIP / 缺 container 结构 → 抛 `ValueError`（无效 EPUB）；解压后超
    `_MAX_UNCOMPRESSED_BYTES` → 抛 `ValueError`。
    """
    try:
        _check_uncompressed_size(data)
        book = epub.read_epub(io.BytesIO(data))
    except (zipfile.BadZipFile, KeyError) as e:
        raise ValueError(policy("reading", "epub.copy.parse_epub_text", str)) from e
    title = _extract_meta(book, "title")
    author = _extract_meta(book, "creator")

    sections = _toc_sections(book.toc)
    segments: list[Segment] = []
    for idref, linear in book.spine:
        if linear == "no":
            continue
        item = book.get_item_with_id(idref)
        if item is None or item.get_type() != ITEM_DOCUMENT:
            continue
        content = item.get_content()
        text = (
            content.decode("utf-8", errors="replace")
            if isinstance(content, bytes)
            else content
        )
        document = posixpath.normpath(item.get_name())
        tree = ElementTree.fromstring(text)
        document_title = "".join(
            tree.findtext(".//{*}head/{*}title", default="")
        ).strip()
        segments.extend(
            segment_html(text, sections.get(document), document, document_title)
        )

    content_hash = hashlib.sha256(
        "\n".join(s.text for s in segments).encode("utf-8")
    ).hexdigest()
    return EpubResult(
        title=title, author=author, segments=segments, content_hash=content_hash
    )


def _extract_meta(book: epub.EpubBook, key: str) -> str:
    """取 dc 元数据第一个元组 `[0]`；缺失/空回退空串。"""
    for item in book.get_metadata("DC", key):
        value = item[0]
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
