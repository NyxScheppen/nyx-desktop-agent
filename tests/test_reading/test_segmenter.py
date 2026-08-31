"""segment_html 纯函数测试（19-reading-content）。"""

from nyx.reading.segmenter import Segment, segment_html


def test_heading_plus_paragraph_merges_and_marks_chapter_start() -> None:
    segments = segment_html("<h2>第一章</h2><p>正文</p>")
    assert segments == [Segment(text="第一章\n正文", is_chapter_start=True)]


def test_h1_alone_marks_chapter_start() -> None:
    segments = segment_html("<h1>序章</h1>")
    assert segments == [Segment(text="序章", is_chapter_start=True)]


def test_h3_is_not_chapter_start() -> None:
    segments = segment_html("<h3>小节</h3>")
    assert segments == [Segment(text="小节", is_chapter_start=False)]


def test_consecutive_li_merge_newline_separated() -> None:
    segments = segment_html("<ul><li>一</li><li>二</li><li>三</li></ul>")
    assert segments == [Segment(text="一\n二\n三", is_chapter_start=False)]


def test_short_paragraphs_merge() -> None:
    segments = segment_html("<p>短。</p><p>也短。</p>")
    assert segments == [Segment(text="短。\n也短。", is_chapter_start=False)]


def test_long_paragraph_splits_at_period() -> None:
    html = "<p>" + "x" * 2000 + "。" + "y" * 2000 + "</p>"
    segments = segment_html(html)
    assert len(segments) == 2
    assert segments[0].text == "x" * 2000 + "。"
    assert segments[1].text == "y" * 2000


def test_fallback_no_block_tags_whole_text() -> None:
    segments = segment_html("<div>纯文本无块级</div>")
    assert segments == [Segment(text="纯文本无块级", is_chapter_start=False)]


def test_empty_html_returns_empty() -> None:
    assert segment_html("") == []


def test_blockquote_independent() -> None:
    html = "<blockquote>引文独立</blockquote><p>正文</p>"
    segments = segment_html(html)
    assert segments == [
        Segment(text="引文独立", is_chapter_start=False),
        Segment(text="正文", is_chapter_start=False),
    ]
