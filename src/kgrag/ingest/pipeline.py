"""入库流水线：加载 → 切分 → 向量化 → 写入 Milvus。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from langchain_core.documents import Document

from kgrag.ingest.loader import iter_files, load_and_split
from kgrag.logging import get_logger
from kgrag.vectorstore import get_vectorstore

logger = get_logger(__name__)


def chunk_id(chunk: Document, namespace: str = "") -> str:
    """基于「来源 + 块序号」生成稳定 ID。

    同一文件重新入库时 ID 不变，Milvus 会按主键覆盖而不是追加，
    从而避免重复数据。``namespace`` 用于隔离不同知识库。
    """
    meta = chunk.metadata
    raw = (
        f"{namespace}|{meta.get('doc_id')}|{meta.get('chunk_index')}"
        f"|{meta.get('sub_index', 0)}"
    )
    return hashlib.sha1(raw.encode()).hexdigest()[:32]


def ingest_path(
    path: str | Path,
    *,
    collection: str | None = None,
    batch_size: int = 64,
    extra_metadata: dict | None = None,
) -> dict:
    """把文件或目录写入向量库。

    Returns
    -------
    dict: ``{"files": n, "chunks": m, "skipped": k}``
    """
    files = iter_files(path)
    if not files:
        logger.warning(f"未找到可处理的文件: {path}")
        return {"files": 0, "chunks": 0, "skipped": 0}

    store = get_vectorstore(collection)
    namespace = collection or ""
    total_chunks = 0
    skipped = 0
    processed_files = 0
    failures: list[dict] = []

    for f in files:
        try:
            chunks = load_and_split(f)
        except Exception as e:  # noqa: BLE001
            logger.error(f"解析失败 {f.name}: {e}")
            skipped += 1
            failures.append({"file": f.name, "error": str(e)})
            continue

        chunks = [c for c in chunks if c.page_content.strip()]
        if not chunks:
            skipped += 1
            continue

        if extra_metadata:
            for c in chunks:
                c.metadata.update(extra_metadata)

        ids = [chunk_id(c, namespace) for c in chunks]
        for i in range(0, len(chunks), batch_size):
            store.add_documents(
                chunks[i : i + batch_size], ids=ids[i : i + batch_size]
            )

        total_chunks += len(chunks)
        processed_files += 1
        logger.info(f"已入库 {f.name}: {len(chunks)} chunks")

    result = {
        "files": processed_files,
        "chunks": total_chunks,
        "skipped": skipped,
        "failures": failures,
    }
    logger.info(f"入库完成: {result}")
    return result


def ingest_documents(docs: list[Document], collection: str | None = None) -> int:
    """直接写入已切分好的 Document 列表。"""
    store = get_vectorstore(collection)
    namespace = collection or ""
    store.add_documents(docs, ids=[chunk_id(d, namespace) for d in docs])
    logger.info(f"已写入 {len(docs)} 个 Document")
    return len(docs)
