"""segment_html 纯函数测试（reading-system spec）。"""

from nyx.reading.segmenter import segment_html
from nyx.types import ParagraphBlock, TextMark


def test_heading_plus_paragraph_merges_and_marks_chapter_start() -> None:
    segments = segment_html("<h2>第一章</h2><p>正文</p>")
    assert len(segments) == 1
    assert segments[0].text == "第一章\n正文"
    assert segments[0].is_chapter_start is True
    assert segments[0].blocks == (
        ParagraphBlock(kind="heading", start=0, end=3, level=2),
        ParagraphBlock(kind="paragraph", start=4, end=6, level=None),
    )


def test_inline_marks_use_utf16_offsets() -> None:
    segments = segment_html(
        "<p>普通<strong>加粗😀</strong><em>斜体</em></p>"
    )
    assert len(segments) == 1
    assert segments[0].text == "普通加粗😀斜体"
    assert segments[0].marks == (
        TextMark(start=2, end=6, bold=True, italic=False),
        TextMark(start=6, end=8, bold=False, italic=True),
    )


def test_fallback_preserves_inline_mark() -> None:
    segments = segment_html("<div><strong>粗体</strong></div>")
    assert segments[0].marks == (
        TextMark(start=0, end=2, bold=True, italic=False),
    )


def test_h1_alone_marks_chapter_start() -> None:
    segments = segment_html("<h1>序章</h1>")
    assert [(s.text, s.is_chapter_start) for s in segments] == [("序章", True)]
    assert segments[0].blocks[0].level == 1


def test_h3_is_not_chapter_start() -> None:
    segments = segment_html("<h3>小节</h3>")
    assert [(s.text, s.is_chapter_start) for s in segments] == [("小节", False)]
    assert segments[0].blocks[0].level == 3


def test_consecutive_li_merge_newline_separated() -> None:
    segments = segment_html("<ul><li>一</li><li>二</li><li>三</li></ul>")
    assert [(s.text, s.is_chapter_start) for s in segments] == [
        ("一\n二\n三", False)
    ]
    assert [block.kind for block in segments[0].blocks] == ["list_item"] * 3


def test_short_paragraphs_merge() -> None:
    segments = segment_html("<p>短。</p><p>也短。</p>")
    assert [(s.text, s.is_chapter_start) for s in segments] == [
        ("短。\n也短。", False)
    ]


def test_long_paragraph_splits_at_period() -> None:
    html = "<p>" + "x" * 2000 + "。" + "y" * 2000 + "</p>"
    segments = segment_html(html)
    assert len(segments) == 2
    assert segments[0].text == "x" * 2000 + "。"
    assert segments[1].text == "y" * 2000


def test_fallback_no_block_tags_whole_text() -> None:
    segments = segment_html("<div>纯文本无块级</div>")
    assert [(s.text, s.is_chapter_start) for s in segments] == [
        ("纯文本无块级", False)
    ]


def test_fallback_no_block_tags_also_splits_long_text() -> None:
    segments = segment_html("。".join(["长"] * 3501))
    assert len(segments) > 1
    assert all(len(segment.text) <= 3000 for segment in segments)


def test_empty_html_returns_empty() -> None:
    assert segment_html("") == []


def test_script_and_style_content_is_ignored() -> None:
    html = (
        "<h2>第一章</h2>"
        "<p>正文前<script>window.secret = '不该出现';</script>"
        "<style>.hidden { display: none; }</style>正文后</p>"
    )
    segments = segment_html(html)
    assert [segment.text for segment in segments] == ["第一章\n正文前正文后"]


def test_blockquote_independent() -> None:
    html = "<blockquote>引文独立</blockquote><p>正文</p>"
    segments = segment_html(html)
    assert [(s.text, s.is_chapter_start) for s in segments] == [
        ("引文独立", False),
        ("正文", False),
    ]
    assert segments[0].blocks[0].kind == "blockquote"


def test_nested_block_direct_text_preserves_document_order() -> None:
    segments = segment_html("<li>引言正文 <p>这是列表项</p></li>")
    assert [s.text for s in segments] == ["引言正文", "这是列表项"]
    assert [s.blocks[0].kind for s in segments] == ["list_item", "list_item"]
    assert all(not s.is_chapter_start for s in segments)


def test_nested_block_tail_text_preserves_document_order() -> None:
    segments = segment_html("<blockquote>引言<p>内文</p>续</blockquote>")
    assert [s.text for s in segments] == ["引言", "内文", "续"]
    assert [s.blocks[0].kind for s in segments] == ["blockquote"] * 3
