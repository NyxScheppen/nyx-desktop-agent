"""HTML 正文分段器：块级元素 → 纯文本段落 + 受控格式。

同步、无 IO、无 LLM，使用标准库 html.parser。只保留当前阅读器会渲染的
块语义和 strong/em 行内标记，不保存 raw HTML、属性或 EPUB CSS。
"""

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import NamedTuple

from nyx.types import ParagraphBlock, ParagraphBlockKind, TextMark


class Segment(NamedTuple):
    """一个阅读段落；格式 offset 统一为 UTF-16 code unit。"""

    text: str
    is_chapter_start: bool
    blocks: tuple[ParagraphBlock, ...] = ()
    marks: tuple[TextMark, ...] = ()


@dataclass(frozen=True)
class _Run:
    text: str
    bold: bool
    italic: bool


@dataclass(frozen=True)
class _Block:
    tag: str
    semantic_tag: str
    runs: tuple[_Run, ...]

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


@dataclass(frozen=True)
class _Draft:
    blocks: tuple[_Block, ...]

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.blocks)


_BLOCK_TAGS = frozenset(
    {"p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "li", "pre"}
)
_BOLD_TAGS = frozenset({"strong", "b"})
_ITALIC_TAGS = frozenset({"em", "i"})
_IGNORED_TAGS = frozenset({"script", "style"})
_SEMANTIC_CONTAINER_TAGS = frozenset({"blockquote", "li", "pre"})


class _BlockExtractor(HTMLParser):
    """按文档序提取块和行内标记，嵌套块只保留直接文本。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_Block] = []
        self._stack: list[tuple[str, str, list[_Run]]] = []
        self.root_runs: list[_Run] = []
        self._bold_depth = 0
        self._italic_depth = 0
        self._ignored_tag: str | None = None

    def _buf(self) -> list[_Run]:
        return self._stack[-1][2] if self._stack else self.root_runs

    def _append(self, text: str) -> None:
        if not text:
            return
        _append_run(
            self._buf(),
            _Run(text, self._bold_depth > 0, self._italic_depth > 0),
        )

    def _flush_direct(self, frame: tuple[str, str, list[_Run]]) -> None:
        tag, semantic_tag, runs = frame
        trimmed = _trim_runs(runs)
        if trimmed:
            self.blocks.append(_Block(tag, semantic_tag, trimmed))
        runs.clear()

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        if self._ignored_tag is not None:
            return
        if tag in _IGNORED_TAGS:
            self._ignored_tag = tag
        elif tag in _BLOCK_TAGS:
            if self._stack:
                self._flush_direct(self._stack[-1])
            semantic_tag = tag
            if tag not in _SEMANTIC_CONTAINER_TAGS:
                parent_semantic_tag = next(
                    (
                        frame[1]
                        for frame in reversed(self._stack)
                        if frame[1] in _SEMANTIC_CONTAINER_TAGS
                    ),
                    None,
                )
                if parent_semantic_tag is not None:
                    semantic_tag = parent_semantic_tag
            self._stack.append((tag, semantic_tag, []))
        elif tag in _BOLD_TAGS:
            self._bold_depth += 1
        elif tag in _ITALIC_TAGS:
            self._italic_depth += 1
        elif tag == "br":
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_tag is not None:
            if tag == self._ignored_tag:
                self._ignored_tag = None
            return
        if tag in _BOLD_TAGS:
            self._bold_depth = max(0, self._bold_depth - 1)
        elif tag in _ITALIC_TAGS:
            self._italic_depth = max(0, self._italic_depth - 1)
        elif tag in _BLOCK_TAGS and self._stack:
            self._flush_direct(self._stack.pop())

    def handle_data(self, data: str) -> None:
        if self._ignored_tag is not None:
            return
        self._append(data)


def segment_html(html: str) -> list[Segment]:
    """HTML → 阅读段落；无块级元素时回退为一个普通正文块。"""
    extractor = _BlockExtractor()
    extractor.feed(html)
    extractor.close()

    if not extractor.blocks:
        runs = _trim_runs(extractor.root_runs)
        if not runs:
            return []
        return _split_long_drafts([_Draft((_Block("p", "p", runs),))])

    drafts = _merge_heading_and_paragraph(extractor.blocks)
    drafts = _merge_consecutive_lists(drafts)
    drafts = _merge_short_paragraphs(drafts)
    return _split_long_drafts(drafts)


def _append_run(runs: list[_Run], run: _Run) -> None:
    if runs and runs[-1].bold == run.bold and runs[-1].italic == run.italic:
        previous = runs[-1]
        runs[-1] = _Run(previous.text + run.text, run.bold, run.italic)
    else:
        runs.append(run)


def _trim_runs(runs: list[_Run]) -> tuple[_Run, ...]:
    text = "".join(run.text for run in runs)
    start = len(text) - len(text.lstrip())
    end = len(text.rstrip())
    return _slice_runs(runs, start, end)


def _slice_runs(runs: list[_Run], start: int, end: int) -> tuple[_Run, ...]:
    result: list[_Run] = []
    cursor = 0
    for run in runs:
        run_end = cursor + len(run.text)
        left = max(start, cursor)
        right = min(end, run_end)
        if left < right:
            _append_run(
                result,
                _Run(
                    run.text[left - cursor:right - cursor],
                    run.bold,
                    run.italic,
                ),
            )
        cursor = run_end
    return tuple(result)


def _merge_heading_and_paragraph(blocks: list[_Block]) -> list[_Draft]:
    """标题与紧邻正文仍共用一个阅读进度段，但保留两个块。"""
    result: list[_Draft] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        if (
            block.tag.startswith("h")
            and index + 1 < len(blocks)
            and blocks[index + 1].tag == "p"
        ):
            result.append(_Draft((block, blocks[index + 1])))
            index += 2
        else:
            result.append(_Draft((block,)))
            index += 1
    return result


def _merge_consecutive_lists(drafts: list[_Draft]) -> list[_Draft]:
    result: list[_Draft] = []
    buffered: list[_Block] = []
    for draft in drafts:
        if len(draft.blocks) == 1 and draft.blocks[0].tag == "li":
            buffered.extend(draft.blocks)
            continue
        if buffered:
            result.append(_Draft(tuple(buffered)))
            buffered.clear()
        result.append(draft)
    if buffered:
        result.append(_Draft(tuple(buffered)))
    return result


def _merge_short_paragraphs(
    drafts: list[_Draft], short_threshold: int = 100
) -> list[_Draft]:
    result: list[_Draft] = []
    buffered: list[_Block] = []

    def flush() -> None:
        if buffered:
            result.append(_Draft(tuple(buffered)))
            buffered.clear()

    for draft in drafts:
        accumulated = sum(len(block.text) for block in buffered) + len(draft.text)
        if (
            len(draft.blocks) == 1
            and draft.blocks[0].tag == "p"
            and accumulated < short_threshold
        ):
            buffered.extend(draft.blocks)
        else:
            flush()
            result.append(draft)
    flush()
    return result


def _split_long_drafts(
    drafts: list[_Draft], max_chars: int = 3000
) -> list[Segment]:
    result: list[Segment] = []
    for draft in drafts:
        result.extend(_split_segment(_draft_to_segment(draft), max_chars))
    return result


def _draft_to_segment(draft: _Draft) -> Segment:
    text_parts: list[str] = []
    blocks: list[ParagraphBlock] = []
    marks: list[TextMark] = []
    cursor = 0
    for index, block in enumerate(draft.blocks):
        if index > 0:
            text_parts.append("\n")
            cursor += 1
        block_start = cursor
        for run in block.runs:
            text_parts.append(run.text)
            run_start = cursor
            cursor += _utf16_len(run.text)
            if run.bold or run.italic:
                mark = TextMark(run_start, cursor, run.bold, run.italic)
                if (
                    marks
                    and marks[-1].end == mark.start
                    and marks[-1].bold == mark.bold
                    and marks[-1].italic == mark.italic
                ):
                    previous = marks[-1]
                    marks[-1] = TextMark(
                        previous.start,
                        mark.end,
                        mark.bold,
                        mark.italic,
                    )
                else:
                    marks.append(mark)
        kind, level = _block_kind(block.semantic_tag)
        blocks.append(ParagraphBlock(kind, block_start, cursor, level))
    first_tag = draft.blocks[0].tag
    return Segment(
        text="".join(text_parts),
        is_chapter_start=first_tag in {"h1", "h2"},
        blocks=tuple(blocks),
        marks=tuple(marks),
    )


def _block_kind(tag: str) -> tuple[ParagraphBlockKind, int | None]:
    if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
        return "heading", int(tag[1])
    if tag == "blockquote":
        return "blockquote", None
    if tag == "li":
        return "list_item", None
    if tag == "pre":
        return "pre", None
    return "paragraph", None


def _split_segment(segment: Segment, max_chars: int) -> list[Segment]:
    if len(segment.text) <= max_chars:
        return [segment]
    result: list[Segment] = []
    cursor = 0
    while len(segment.text) - cursor > max_chars:
        window = segment.text[cursor:cursor + max_chars]
        split_at = window.rfind("。")
        if split_at == -1:
            split_at = window.rfind(".")
        split_at = max_chars if split_at == -1 else split_at + 1
        raw = segment.text[cursor:cursor + split_at]
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw.rstrip())
        if leading < trailing:
            result.append(_slice_segment(segment, cursor + leading, cursor + trailing))
        cursor += split_at
        cursor += len(segment.text[cursor:]) - len(segment.text[cursor:].lstrip())
    if cursor < len(segment.text):
        result.append(_slice_segment(segment, cursor, len(segment.text)))
    return result


def _slice_segment(segment: Segment, start: int, end: int) -> Segment:
    utf16_start = _utf16_len(segment.text[:start])
    utf16_end = _utf16_len(segment.text[:end])
    blocks = tuple(
        ParagraphBlock(
            block.kind,
            max(block.start, utf16_start) - utf16_start,
            min(block.end, utf16_end) - utf16_start,
            block.level,
        )
        for block in segment.blocks
        if block.end > utf16_start and block.start < utf16_end
    )
    marks = tuple(
        TextMark(
            max(mark.start, utf16_start) - utf16_start,
            min(mark.end, utf16_end) - utf16_start,
            mark.bold,
            mark.italic,
        )
        for mark in segment.marks
        if mark.end > utf16_start and mark.start < utf16_end
    )
    return Segment(
        text=segment.text[start:end],
        is_chapter_start=segment.is_chapter_start,
        blocks=blocks,
        marks=marks,
    )


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2
