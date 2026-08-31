"""parse_epub 单元测试（19-reading-content）：内存构造 EPUB 字节。"""

import io
import zipfile

from nyx.reading.epub import parse_epub
from nyx.reading.segmenter import Segment

_CONTAINER_XML = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="EPUB/content.opf" '
    'media-type="application/oebps-package+xml"/></rootfiles></container>'
)

_CHAPTER1 = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
    '<h2>第一章</h2><p>第一段正文。</p>'
    '</body></html>'
)

_CHAPTER2 = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
    '<h2>第二章</h2><p>第二段正文。</p>'
    '</body></html>'
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
        '<manifest>'
        '<item href="chapter1.xhtml" id="chapter1" '
        'media-type="application/xhtml+xml"/>'
        '<item href="chapter2.xhtml" id="chapter2" '
        'media-type="application/xhtml+xml"/>'
        '<item href="images/cover.jpg" id="cover-image" media-type="image/jpeg"/>'
        '</manifest>'
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
    assert result.segments == [
        Segment(text="第一章\n第一段正文。", is_chapter_start=True),
        Segment(text="第二章\n第二段正文。", is_chapter_start=True),
    ]


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
    assert len(result.segments) == 2
