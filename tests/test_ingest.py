import pytest

from kgrag.ingest import load_and_split, load_file, split_document
from kgrag.ingest.loader import iter_files


def test_load_markdown(sample_md):
    doc = load_file(sample_md)
    assert "产品手册" in doc.page_content
    assert doc.metadata["filename"] == "sample.md"
    assert doc.metadata["doc_id"]


def test_load_txt(sample_txt):
    doc = load_file(sample_txt)
    assert "Milvus" in doc.page_content


def test_load_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_file(tmp_path / "nope.txt")


def test_load_unsupported_suffix(tmp_path):
    p = tmp_path / "a.xyz"
    p.write_text("x")
    with pytest.raises(ValueError, match="不支持"):
        load_file(p)


def test_markdown_split_by_header(sample_md):
    chunks = load_and_split(sample_md)
    assert len(chunks) >= 2
    # 标题被保留为元数据
    assert any("h1" in c.metadata or "h2" in c.metadata for c in chunks)
    for c in chunks:
        assert "filename" in c.metadata
        assert "chunk_index" in c.metadata


def test_long_text_split(tmp_path):
    p = tmp_path / "long.txt"
    p.write_text("这是一个句子。" * 500, encoding="utf-8")
    chunks = load_and_split(p)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.page_content) <= 200 * 2  # chunk_size=200，允许一定余量


def test_split_document_small_stays_whole(sample_txt):
    doc = load_file(sample_txt)
    chunks = split_document(doc)
    assert len(chunks) == 1


def test_iter_files(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.md").write_text("b")
    (tmp_path / "c.xyz").write_text("c")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "d.txt").write_text("d")

    files = iter_files(tmp_path)
    names = {f.name for f in files}
    assert names == {"a.txt", "b.md", "d.txt"}


def test_iter_files_single_file(sample_txt):
    files = iter_files(sample_txt)
    assert files == [sample_txt]


# --------------------------------------------------------------------------- #
# 需求 3.1：多种格式解析（txt / word / ppt / excel / html / md / xml）
# --------------------------------------------------------------------------- #
def test_load_html(tmp_path):
    p = tmp_path / "page.html"
    p.write_text("<html><body><h1>标题</h1><p>正文内容</p><script>x()</script></body></html>",
                 encoding="utf-8")
    doc = load_file(p)
    assert "标题" in doc.page_content
    assert "正文内容" in doc.page_content
    assert "x()" not in doc.page_content  # script 被剔除


def test_load_xml(tmp_path):
    p = tmp_path / "data.xml"
    p.write_text("<root><item id='1'>苹果</item><item id='2'>香蕉</item></root>",
                 encoding="utf-8")
    doc = load_file(p)
    assert "苹果" in doc.page_content
    assert "香蕉" in doc.page_content


def test_load_docx(tmp_path):
    """手工构造一个最小 docx（zip + word/document.xml），不依赖 python-docx。"""
    import zipfile

    p = tmp_path / "doc.docx"
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>Word 正文内容</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/></Types>',
        )
        z.writestr("word/document.xml", document_xml)

    doc = load_file(p)
    assert "Word 正文内容" in doc.page_content


def test_load_pptx(tmp_path):
    from pptx import Presentation

    p = tmp_path / "deck.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "幻灯片标题"
    slide.placeholders[1].text = "幻灯片要点"
    prs.save(str(p))
    doc = load_file(p)
    assert "幻灯片标题" in doc.page_content
    assert "幻灯片要点" in doc.page_content


def test_load_xlsx(tmp_path):
    from openpyxl import Workbook

    p = tmp_path / "sheet.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "数据"
    ws.append(["姓名", "数量"])
    ws.append(["苹果", 10])
    wb.save(str(p))
    doc = load_file(p)
    assert "数据" in doc.page_content
    assert "苹果" in doc.page_content


def test_load_and_split_all_supported(tmp_path):
    """端到端：各格式都能加载并切分。"""
    for suffix, content in [(".txt", "纯文本"), (".md", "# 标题\n正文"), (".xml", "<a>内容</a>")]:
        p = tmp_path / f"f{suffix}"
        p.write_text(content, encoding="utf-8")
        chunks = load_and_split(p)
        assert chunks, f"{suffix} 没有产出切块"
        assert all(c.page_content for c in chunks)
