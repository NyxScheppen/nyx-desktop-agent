"""parse_epub 单元测试（reading-system spec）：内存构造 EPUB 字节。"""

import io
import zipfile

import pytest

from nyx.reading import epub as epub_mod
from nyx.reading.epub import parse_epub

_CONTAINER_XML = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="EPUB/content.opf" '
    'media-type="application/oebps-package+xml"/></rootfiles></container>'
)

_CHAPTER1 = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
    "<h2>第一章</h2><p>第一段正文。</p>"
    "</body></html>"
)

_CHAPTER2 = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
    "<h2>第二章</h2><p>第二段正文。</p>"
    "</body></html>"
)


def _build_epub_bytes(
    *,
    title: str | None = "测试书名",
    author: str | None = "测试作者",
    image_in_spine: bool = False,
) -> bytes:
    """构造最小 EPUB：chapter1 + chapter2 两文档 + 可选封面图进 spine。"""
    title_xml = f"<dc:title>{title}</dc:title>" if title else ""
    author_xml = f"<dc:creator>{author}</dc:creator>" if author else ""
    cover_itemref = '<itemref idref="cover-image"/>' if image_in_spine else ""

    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" '
        'unique-identifier="id" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="id">nyx-test-001</dc:identifier>'
        f"{title_xml}{author_xml}<dc:language>zh</dc:language></metadata>"
        "<manifest>"
        '<item href="chapter1.xhtml" id="chapter1" '
        'media-type="application/xhtml+xml"/>'
        '<item href="chapter2.xhtml" id="chapter2" '
        'media-type="application/xhtml+xml"/>'
        '<item href="images/cover.jpg" id="cover-image" media-type="image/jpeg"/>'
        "</manifest>"
        '<spine><itemref idref="chapter1"/><itemref idref="chapter2"/>'
        f"{cover_itemref}</spine></package>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED
        )
        zf.writestr("META-INF/container.xml", _CONTAINER_XML)
        zf.writestr("EPUB/content.opf", opf)
        zf.writestr("EPUB/chapter1.xhtml", _CHAPTER1)
        zf.writestr("EPUB/chapter2.xhtml", _CHAPTER2)
        zf.writestr("EPUB/images/cover.jpg", b"\xff\xd8\xff\xe0\x00\x10")
    return buf.getvalue()


def test_parse_epub_extracts_title_author_and_segments() -> None:
    result = parse_epub(_build_epub_bytes())
    assert result.title == "测试书名"
    assert result.author == "测试作者"
    assert [(s.text, s.is_chapter_start) for s in result.segments] == [
        ("第一章", True),
        ("第一段正文。", False),
        ("第二章", True),
        ("第二段正文。", False),
    ]
    assert result.segments[0].blocks[0].kind == "heading"


def test_parse_epub_content_hash_stable() -> None:
    data = _build_epub_bytes()
    assert parse_epub(data).content_hash == parse_epub(data).content_hash
    assert len(parse_epub(data).content_hash) == 64  # SHA-256 hex


def test_parse_epub_missing_metadata_falls_back_to_empty() -> None:
    result = parse_epub(_build_epub_bytes(title=None, author=None))
    assert result.title == ""
    assert result.author == ""


def test_parse_epub_skips_non_document_spine_items() -> None:
    # 封面图进 spine：parse_epub 只读 ITEM_DOCUMENT，段数不受图片影响
    result = parse_epub(_build_epub_bytes(image_in_spine=True))
    assert len(result.segments) == 4


def _epub_with_promotional_front_matter(*, include_ads: bool) -> bytes:
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("front-matter-filter")
    book.set_title("正文筛选")
    book.set_language("zh")
    front = epub.EpubHtml(title="出版信息", file_name="title.xhtml", lang="zh")
    front.content = "<p>版权所有。</p>".encode()
    book.add_item(front)
    book.spine = [front]
    if include_ads:
        ad = epub.EpubHtml(title="广告", file_name="ad.xhtml", lang="zh")
        ad.content = "<p>某出版社近期新书。</p>".encode()
        book.add_item(ad)
        book.spine.append(ad)

    preface = epub.EpubHtml(title="序言", file_name="preface.xhtml", lang="zh")
    promotion = "<p>关注公众号获取更多内容</p>" if include_ads else ""
    preface.content = (
        f"<h2>序言</h2>{promotion}<p>读者当时通过扫码取得了调查问卷。</p>"
    ).encode()
    chapter = epub.EpubHtml(title="第一章", file_name="chapter.xhtml", lang="zh")
    chapter.content = (
        "<h2>第一章</h2><p><em>正文</em>开始。</p><p>请扫码！</p>"
    ).encode()
    book.add_item(preface)
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.spine.extend([preface, chapter])
    book.toc = [
        epub.Link("preface.xhtml", "序言", "preface"),
        epub.Link("chapter.xhtml", "第一章", "chapter"),
    ]
    output = io.BytesIO()
    epub.write_epub(output, book)
    return output.getvalue()


def test_parse_epub_skips_promotions_before_opening_chapter() -> None:
    result = parse_epub(_epub_with_promotional_front_matter(include_ads=True))
    clean = parse_epub(_epub_with_promotional_front_matter(include_ads=False))
    assert [segment.text for segment in result.segments] == [
        "版权所有。",
        "序言",
        "读者当时通过扫码取得了调查问卷。",
        "第一章",
        "正文开始。",
        "请扫码！",
    ]
    assert result.segments == clean.segments
    assert result.content_hash == clean.content_hash
    assert result.segments[3].is_chapter_start
    assert result.segments[4].marks[0].italic


def test_parse_epub_without_opening_chapter_stops_at_unclassified_text() -> None:
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("unmarked-opening")
    book.set_title("短篇集")
    book.set_language("zh")
    story = epub.EpubHtml(title="短篇集", file_name="story.xhtml", lang="zh")
    story.content = (
        "<p>请扫码！</p><p>雨下了一整夜。</p><p>请扫码！</p>"
    ).encode()
    book.add_item(story)
    book.add_item(epub.EpubNcx())
    book.spine = [story]
    output = io.BytesIO()
    epub.write_epub(output, book)

    result = parse_epub(output.getvalue())
    assert [segment.text for segment in result.segments] == [
        "雨下了一整夜。",
        "请扫码！",
    ]
    assert result.segments[0].is_chapter_start


def test_parse_epub_non_zip_raises_value_error() -> None:
    with pytest.raises(ValueError):
        parse_epub(b"not a zip file")


def test_parse_epub_zip_without_container_raises_value_error() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("random.txt", "hello")
    with pytest.raises(ValueError):
        parse_epub(buf.getvalue())


def test_parse_epub_rejects_oversized_uncompressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(epub_mod, "_MAX_UNCOMPRESSED_BYTES", 100)
    with pytest.raises(ValueError):
        parse_epub(_build_epub_bytes())


def test_toc_anchors_keep_prefaces_separate_without_heading_keywords() -> None:
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("toc-prefaces")
    book.set_title("结构来源")
    book.set_language("zh")
    chapter = epub.EpubHtml(title="文档标题", file_name="prefaces.xhtml", lang="zh")
    chapter.content = (
        '<p>未归属的内容</p><p id="translator">译者开场</p><p>我们重新翻译。</p>'
        '<p id="author">作者开场</p><p>我们研究旧形式。</p>'
    ).encode()
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.spine = [chapter]
    book.toc = [
        epub.Link("prefaces.xhtml#translator", "英译者序", "translator"),
        epub.Link("prefaces.xhtml#author", "作者序", "author"),
    ]
    output = io.BytesIO()
    epub.write_epub(output, book)
    result = parse_epub(output.getvalue())
    starts = [s for s in result.segments if s.is_chapter_start]
    assert [
        s.section.title for s in starts if s.section and s.section.evidence == "toc"
    ] == ["英译者序", "作者序"]
    assert all(
        "重新翻译" not in s.text
        for s in result.segments
        if s.section and s.section.title == "作者序"
    )
    assert all(s.section and s.section.document == "prefaces.xhtml" for s in starts)
