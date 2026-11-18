"""Unit tests for app.utils.file_handler helpers and loaders.

Real file I/O is limited to tmp_path fixtures; anything touching external
document parsers (PDF/PPTX) is only exercised on error/empty paths so the
tests stay dependency-light and offline-friendly.
"""
import hashlib
import io
import os

import pytest

from app.utils.file_handler import (
    FontBBoxStreamFilter,
    get_file_md5_hex,
    get_file_md5_hex_sync,
    listdir_allowed_type,
    markdown_loader,
    markdown_loader_sync,
    ppt_loader,
    ppt_loader_sync,
    txt_loader,
    txt_loader_sync,
    word_loader,
    word_loader_sync,
)


# ---------------------------------------------------------------------------
# md5 helpers
# ---------------------------------------------------------------------------
def test_get_file_md5_hex_sync_matches_hashlib(tmp_path):
    p = tmp_path / "file.bin"
    p.write_bytes(bytes(range(256)) * 16)
    assert get_file_md5_hex_sync(str(p)) == hashlib.md5(p.read_bytes()).hexdigest()


async def test_get_file_md5_hex_async_matches_hashlib(tmp_path):
    p = tmp_path / "file.bin"
    p.write_bytes(b"hello world")
    assert await get_file_md5_hex(str(p)) == hashlib.md5(b"hello world").hexdigest()


def test_get_file_md5_hex_sync_missing_file_returns_empty(tmp_path):
    assert get_file_md5_hex_sync(str(tmp_path / "nope.bin")) == ""


async def test_get_file_md5_hex_async_missing_file_returns_empty(tmp_path):
    assert await get_file_md5_hex(str(tmp_path / "nope.bin")) == ""


def test_get_file_md5_hex_sync_directory_returns_empty(tmp_path):
    assert get_file_md5_hex_sync(str(tmp_path)) == ""


async def test_get_file_md5_hex_async_directory_returns_empty(tmp_path):
    assert await get_file_md5_hex(str(tmp_path)) == ""


def test_get_file_md5_hex_sync_empty_file(tmp_path):
    p = tmp_path / "empty.bin"
    p.write_bytes(b"")
    assert get_file_md5_hex_sync(str(p)) == hashlib.md5(b"").hexdigest()


# ---------------------------------------------------------------------------
# listdir_allowed_type
# ---------------------------------------------------------------------------
async def test_listdir_allowed_type_filters_by_extension(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.pdf").write_text("b")
    (tmp_path / "c.md").write_text("c")
    (tmp_path / "d.png").write_text("d")
    (tmp_path / "e.TXT").write_text("e")  # case-sensitive -> excluded
    subdir = tmp_path / "sub"
    subdir.mkdir()
    (subdir / "f.txt").write_text("f")  # nested -> excluded

    result = await listdir_allowed_type(str(tmp_path), (".txt", ".pdf"))
    assert isinstance(result, tuple)
    names = sorted(str(p).replace("\\", "/").rsplit("/", 1)[-1] for p in result)
    assert names == ["a.txt", "b.pdf"]
    # returned paths are absolute and point at real files
    for p in result:
        assert p.startswith(str(tmp_path))
        assert os.path.isfile(p)


async def test_listdir_allowed_type_nonexistent_dir_returns_empty(tmp_path):
    assert await listdir_allowed_type(str(tmp_path / "nope"), (".txt",)) == ()


async def test_listdir_allowed_type_file_path_returns_empty(tmp_path):
    p = tmp_path / "single.txt"
    p.write_text("x")
    assert await listdir_allowed_type(str(p), (".txt",)) == ()


async def test_listdir_allowed_type_empty_dir_returns_empty(tmp_path):
    assert await listdir_allowed_type(str(tmp_path), (".txt",)) == ()


# ---------------------------------------------------------------------------
# text loaders
# ---------------------------------------------------------------------------
def test_txt_loader_sync_utf8(tmp_path):
    p = tmp_path / "note.txt"
    content = "第一行\nsecond line\n第三行"
    p.write_text(content, encoding="utf-8")
    docs = txt_loader_sync(str(p))
    assert len(docs) == 1
    assert content in docs[0].page_content


def test_txt_loader_sync_gbk_fallback(tmp_path):
    p = tmp_path / "gbk.txt"
    content = "你好世界中文内容"
    p.write_text(content, encoding="gbk")
    docs = txt_loader_sync(str(p))
    assert len(docs) == 1
    assert content.strip() in docs[0].page_content


async def test_txt_loader_async_roundtrip(tmp_path):
    p = tmp_path / "note.txt"
    content = "async loader content"
    p.write_text(content, encoding="utf-8")
    docs = await txt_loader(str(p))
    assert len(docs) == 1
    assert content in docs[0].page_content


def test_txt_loader_sync_missing_file_returns_empty(tmp_path):
    assert txt_loader_sync(str(tmp_path / "nope.txt")) == []


async def test_txt_loader_async_missing_file_returns_empty(tmp_path):
    assert await txt_loader(str(tmp_path / "nope.txt")) == []


# ---------------------------------------------------------------------------
# other loaders: error path only (no external parsers invoked)
# ---------------------------------------------------------------------------
def test_word_loader_sync_missing_file_returns_empty(tmp_path):
    assert word_loader_sync(str(tmp_path / "nope.docx")) == []


async def test_word_loader_async_missing_file_returns_empty(tmp_path):
    assert await word_loader(str(tmp_path / "nope.docx")) == []


def test_markdown_loader_sync_missing_file_returns_empty(tmp_path):
    assert markdown_loader_sync(str(tmp_path / "nope.md")) == []


async def test_markdown_loader_async_missing_file_returns_empty(tmp_path):
    assert await markdown_loader(str(tmp_path / "nope.md")) == []


def test_ppt_loader_sync_missing_file_returns_empty(tmp_path):
    assert ppt_loader_sync(str(tmp_path / "nope.pptx")) == []


async def test_ppt_loader_async_missing_file_returns_empty(tmp_path):
    assert await ppt_loader(str(tmp_path / "nope.pptx")) == []


# ---------------------------------------------------------------------------
# markdown / word 加载（纯文本快路径，不依赖 unstructured）
# ---------------------------------------------------------------------------
async def test_markdown_loader_reads_utf8_without_unstructured(tmp_path):
    path = tmp_path / "policy.md"
    path.write_text("# 标题\n\n一线城市住宿标准为 600 元。", encoding="utf-8")

    docs = await markdown_loader(str(path))

    assert len(docs) == 1
    assert "一线城市住宿标准为 600 元" in docs[0].page_content


def test_markdown_loader_sync_reads_tables(tmp_path):
    path = tmp_path / "policy.md"
    path.write_text("| 城市 | 标准 |\n| --- | --- |\n| 北京 | 600 |", encoding="utf-8")

    docs = markdown_loader_sync(str(path))

    assert "| 北京 | 600 |" in docs[0].page_content


async def test_word_loader_extracts_paragraphs_and_tables(tmp_path):
    from docx import Document as DocxDocument

    path = tmp_path / "handbook.docx"
    doc = DocxDocument()
    doc.add_heading("员工手册", level=1)
    doc.add_paragraph("年假按工龄计算。")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "城市"
    table.cell(0, 1).text = "标准"
    table.cell(1, 0).text = "北京"
    table.cell(1, 1).text = "600"
    doc.save(path)

    docs = await word_loader(str(path))

    assert len(docs) == 1
    text = docs[0].page_content
    assert "员工手册" in text and "年假按工龄计算" in text
    # 表格按行输出，单元格以制表符分隔
    assert "北京\t600" in text


def test_word_loader_returns_empty_for_non_docx(tmp_path):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a real docx")

    assert word_loader_sync(str(path)) == []

def test_font_bbox_filter_suppresses_fontbox_lines():
    sink = io.StringIO()
    stream = FontBBoxStreamFilter(sink)
    stream.write("normal log line\n")
    stream.write("FontBBox from font descriptor (suppressed)\n")
    stream.write("more normal\n")
    out = sink.getvalue()
    assert "normal log line" in out
    assert "more normal" in out
    assert "FontBBox" not in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
