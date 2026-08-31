"""EPUB 解析：字节 → 元数据 + 段落 + 内容哈希（spec 19）。同步、无 LLM。

ebooklib 无类型标注，本文件对它的返回值按 untyped 第三方处理（见文件头
pyright 豁免）。title/author 缺失回退空串——title 的 filename 回退由
`ReadingFacade.import_book` 负责（`parse_epub` 只拿 bytes、不知文件名）。
"""
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
import hashlib
import io
from dataclasses import dataclass

from ebooklib import ITEM_DOCUMENT, epub

from nyx.reading.segmenter import Segment, segment_html


@dataclass
class EpubResult:
    """EPUB 解析结果。title/author 缺失时为空串。"""

    title: str
    author: str
    segments: list[Segment]
    content_hash: str


def parse_epub(data: bytes) -> EpubResult:
    """EPUB 字节 → 元数据 + 正文段落 + 全文 SHA-256。

    按 spine 阅读序遍历，跳过 `linear=='no'` 与非 `ITEM_DOCUMENT`（图片/CSS）。
    """
    book = epub.read_epub(io.BytesIO(data))
    title = _extract_meta(book, "title")
    author = _extract_meta(book, "creator")

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
        segments.extend(segment_html(text))

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
