"""
文档加载与切分。

支持格式：txt / md / pdf / docx / html / json / csv
设计上把「加载」与「切分」分开，后续接更多解析器（OCR、表格、代码）时
只需扩展 ``_LOADERS``。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from kgrag.config import get_settings
from kgrag.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".pdf",
    ".docx",
    ".doc",
    ".html",
    ".htm",
    ".json",
    ".csv",
    ".xml",
    ".pptx",
    ".xlsx",
    ".xls",
}


# --------------------------------------------------------------------------- #
# 加载
# --------------------------------------------------------------------------- #
def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _load_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _load_docx(path: Path) -> str:
    import docx2txt

    return docx2txt.process(str(path)) or ""


def _load_html(path: Path) -> str:
    import re

    raw = path.read_text(encoding="utf-8", errors="ignore")
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"[ \t]+", " ", raw)


def _load_json(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    return json.dumps(data, ensure_ascii=False, indent=2)


def _load_xml(path: Path) -> str:
    """XML 转纯文本：保留元素层级和属性，去掉标签噪音。"""
    import xml.etree.ElementTree as ET

    try:
        tree = ET.parse(str(path))
    except ET.ParseError:
        # 不是合法 XML 时退化为纯文本
        return path.read_text(encoding="utf-8", errors="ignore")

    lines: list[str] = []

    def walk(elem, depth: int = 0) -> None:
        indent = "  " * depth
        attrs = " ".join(f'{k}="{v}"' for k, v in elem.attrib.items())
        text = (elem.text or "").strip()
        head = f"{indent}<{elem.tag}{' ' + attrs if attrs else ''}>"
        if text or not len(elem):
            lines.append(f"{head} {text}".rstrip())
        else:
            lines.append(head)
        for child in elem:
            walk(child, depth + 1)
        tail = (elem.tail or "").strip()
        if tail:
            lines.append(f"{indent}{tail}")

    walk(tree.getroot())
    return "\n".join(lines)


def _load_pptx(path: Path) -> str:
    """PPT 逐页提取：每页标题、文本框、表格。"""
    from pptx import Presentation

    prs = Presentation(str(path))
    parts: list[str] = []

    for idx, slide in enumerate(prs.slides, 1):
        parts.append(f"# 第 {idx} 页")
        for shape in slide.shapes:
            if shape.has_text_frame:
                text = "\n".join(
                    p.text.strip() for p in shape.text_frame.paragraphs if p.text.strip()
                )
                if text:
                    parts.append(text)
            if getattr(shape, "has_table", False) and shape.has_table:
                rows = []
                for row in shape.table.rows:
                    rows.append(" | ".join(cell.text.strip() for cell in row.cells))
                if rows:
                    parts.append("\n".join(rows))
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"备注：{notes}")

    return "\n\n".join(parts)


def _load_xlsx(path: Path) -> str:
    """Excel 逐表提取，每个 sheet 输出为「表头 + 行」的文本。"""
    from openpyxl import load_workbook

    wb = load_workbook(str(path), data_only=True, read_only=True)
    parts: list[str] = []
    try:
        for ws in wb.worksheets:
            parts.append(f"# 工作表：{ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = ["" if c is None else str(c) for c in row]
                if not any(c.strip() for c in cells):
                    continue
                parts.append(" | ".join(cells).rstrip(" |"))
    finally:
        wb.close()
    return "\n".join(parts)


_LOADERS: dict[str, Callable[[Path], str]] = {
    ".txt": _load_text,
    ".md": _load_text,
    ".markdown": _load_text,
    ".pdf": _load_pdf,
    ".docx": _load_docx,
    ".doc": _load_docx,
    ".html": _load_html,
    ".htm": _load_html,
    ".json": _load_json,
    ".csv": _load_text,
    ".xml": _load_xml,
    ".pptx": _load_pptx,
    ".xlsx": _load_xlsx,
    ".xls": _load_xlsx,
}


def load_file(path: str | Path) -> Document:
    """加载单个文件为 Document（未切分）。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    suffix = p.suffix.lower()
    loader = _LOADERS.get(suffix)
    if loader is None:
        raise ValueError(f"不支持的文件类型: {suffix}（支持 {sorted(SUPPORTED_SUFFIXES)}）")

    content = loader(p)
    if not content.strip():
        logger.warning(f"文件内容为空，跳过: {p}")

    return Document(
        page_content=content,
        metadata={
            "source": str(p),
            "filename": p.name,
            "suffix": suffix,
            "doc_id": _file_id(p),
        },
    )


def _file_id(path: Path) -> str:
    """基于路径 + 大小 + mtime 的稳定 ID。"""
    st = path.stat()
    raw = f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# 切分
# --------------------------------------------------------------------------- #
def split_document(doc: Document) -> list[Document]:
    """按配置切分文档；Markdown 先按标题结构切，再递归切分。"""
    s = get_settings()
    chunks: list[Document] = []

    if doc.metadata.get("suffix") in {".md", ".markdown"}:
        md_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")],
            strip_headers=False,
        )
        sections = md_splitter.split_text(doc.page_content)
        for sec in sections:
            sec.metadata = {**doc.metadata, **sec.metadata}
        chunks = sections or [doc]
    else:
        chunks = [doc]

    recursive = RecursiveCharacterTextSplitter(
        chunk_size=s.chunk_size,
        chunk_overlap=s.chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", ". ", " ", ""],
        add_start_index=True,
    )

    final: list[Document] = []
    for chunk in chunks:
        if len(chunk.page_content) <= s.chunk_size:
            final.append(chunk)
            continue
        for i, sub in enumerate(recursive.split_documents([chunk])):
            sub.metadata = {**chunk.metadata, "sub_index": i}
            final.append(sub)

    for i, c in enumerate(final):
        c.metadata.setdefault("chunk_index", i)
    return final


def load_and_split(path: str | Path) -> list[Document]:
    doc = load_file(path)
    return split_document(doc)


def iter_files(root: str | Path) -> list[Path]:
    """递归收集目录下受支持的文件。"""
    root = Path(root)
    if root.is_file():
        return [root]
    files = [
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    ]
    return sorted(files)
