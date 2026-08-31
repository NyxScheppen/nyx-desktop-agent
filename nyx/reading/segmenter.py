"""HTML 正文分段器：块级元素 → 阅读段落（纯文本 + 章首标记）。

spec 19：照搬参考项目 S03 `segment_html` 语义，输出收窄为纯文本
（`Segment = (text, is_chapter_start)`，不保留 tag/raw_html）。纯函数：
同步、无 IO、无 LLM，用标准库 `html.parser`（不引入 bs4/lxml 依赖）。

分段规则（按优先级）：
1. 块级元素（`p`/`h1`-`h6`/`blockquote`/`li`/`pre`）各成段；
2. `h1`-`h6` 与紧随的 `p` 合并（`"标题\n正文"` 一段）；
3. 连续 `li` 合并（换行分隔）；
4. 连续短 `p`（累计 < 100 字符）合并；
5. 单段 > 3000 字符在最后一个句号（。或 .）处拆；
6. 无结构化标签则全文一段。
"""

from html.parser import HTMLParser
from typing import NamedTuple


class Segment(NamedTuple):
    """一个阅读段落。`is_chapter_start` = 以 `h1`/`h2` 开头（22 章末检测用）。"""

    text: str
    is_chapter_start: bool


_BLOCK_TAGS = frozenset(
    {"p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "li", "pre"}
)


class _BlockExtractor(HTMLParser):
    """提取块级元素文本（文档序）；嵌套块只取最内层、不重复计数。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []  # (tag, text)
        self._stack: list[list[str]] = []
        self.root_text: list[str] = []  # 块级之外（head/script/纯文本），回退用

    def _buf(self) -> list[str]:
        return self._stack[-1] if self._stack else self.root_text

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs  # 块级提取不关心属性；保留签名以匹配 HTMLParser 接口
        if tag in _BLOCK_TAGS:
            self._stack.append([])
        elif tag == "br":
            self._buf().append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS and self._stack:
            text = "".join(self._stack.pop()).strip()
            if text:
                self.blocks.append((tag, text))

    def handle_data(self, data: str) -> None:
        self._buf().append(data)


def segment_html(html: str) -> list[Segment]:
    """HTML → 阅读段落。无块级元素时回退为全文一段；全空返回 []。"""
    extractor = _BlockExtractor()
    extractor.feed(html)
    extractor.close()

    if not extractor.blocks:
        text = "".join(extractor.root_text).strip()
        if not text:
            return []
        return [Segment(text=text, is_chapter_start=False)]

    with_heading_merge = _merge_heading_and_paragraph(extractor.blocks)
    with_list_merge = _merge_consecutive_lists(with_heading_merge)
    with_short_merge = _merge_short_paragraphs(with_list_merge)
    merged = _split_long_paragraphs(with_short_merge)

    return [
        Segment(text=text, is_chapter_start=_is_chapter_start(tag))
        for text, tag in merged
    ]


def _is_chapter_start(tag: str) -> bool:
    """`h1`/`h2`（或其合并段 `h2+p`）开头 → 章首。"""
    return tag.startswith("h1") or tag.startswith("h2")


def _merge_heading_and_paragraph(
    blocks: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """规则 2：`h1`-`h6` 与紧随的 `p` 合并为 `"标题\n正文"`。"""
    result: list[tuple[str, str]] = []
    skip_next = False
    for i, (tag, text) in enumerate(blocks):
        if skip_next:
            skip_next = False
            continue
        if tag.startswith("h") and i + 1 < len(blocks):
            next_tag, next_text = blocks[i + 1]
            if next_tag == "p":
                result.append((f"{text}\n{next_text}", f"{tag}+p"))
                skip_next = True
                continue
        result.append((text, tag))
    return result


def _merge_consecutive_lists(
    segments: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """规则 3：连续 `li` 合并为一段（换行分隔）。"""
    result: list[tuple[str, str]] = []
    buffer: list[str] = []
    for text, tag in segments:
        if tag == "li":
            buffer.append(text)
        else:
            if buffer:
                result.append(("\n".join(buffer), "li-group"))
                buffer = []
            result.append((text, tag))
    if buffer:
        result.append(("\n".join(buffer), "li-group"))
    return result


def _merge_short_paragraphs(
    segments: list[tuple[str, str]], short_threshold: int = 100
) -> list[tuple[str, str]]:
    """规则 4：连续短 `p`（累计 < 阈值）合并。"""
    result: list[tuple[str, str]] = []
    buffer_texts: list[str] = []
    buffer_tags: list[str] = []

    def flush() -> None:
        if buffer_texts:
            result.append(("\n".join(buffer_texts), "+".join(buffer_tags)))
            buffer_texts.clear()
            buffer_tags.clear()

    for text, tag in segments:
        accumulated = sum(len(t) for t in buffer_texts) + len(text)
        if tag == "p" and accumulated < short_threshold:
            buffer_texts.append(text)
            buffer_tags.append(tag)
        else:
            flush()
            result.append((text, tag))
    flush()
    return result


def _split_long_paragraphs(
    segments: list[tuple[str, str]], max_chars: int = 3000
) -> list[tuple[str, str]]:
    """规则 5：> `max_chars` 的段落在最后一个句号（。或 .）处拆分；无句号硬切。"""
    result: list[tuple[str, str]] = []
    for text, tag in segments:
        while len(text) > max_chars:
            split_at = text.rfind("。", 0, max_chars)
            if split_at == -1:
                split_at = text.rfind(".", 0, max_chars)
            if split_at == -1:
                split_at = max_chars
            else:
                split_at += 1  # 包含句号
            result.append((text[:split_at].strip(), tag))
            text = text[split_at:].lstrip()
        if text:
            result.append((text, tag))
    return result
