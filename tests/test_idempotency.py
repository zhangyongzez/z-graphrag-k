
from kgrag.ingest import ingest_path
from kgrag.ingest.pipeline import chunk_id
from kgrag.vectorstore import get_vectorstore


def _row_count() -> int:
    """直接读集合行数，绕过 embedding（避免重复加载模型）。"""
    from pymilvus import MilvusClient

    from kgrag.config import get_settings

    s = get_settings()
    client = MilvusClient(s.vectorstore_lite_path)
    try:
        stats = client.get_collection_stats(s.vectorstore_collection)
        return int(stats["row_count"])
    except Exception:  # noqa: BLE001 - 集合不存在
        return 0
    finally:
        client.close()


def test_chunk_id_is_stable():
    from langchain_core.documents import Document

    d1 = Document(page_content="a", metadata={"doc_id": "x", "chunk_index": 0})
    d2 = Document(page_content="a", metadata={"doc_id": "x", "chunk_index": 0})
    assert chunk_id(d1) == chunk_id(d2)


def test_chunk_id_differs_by_index():
    from langchain_core.documents import Document

    d1 = Document(page_content="a", metadata={"doc_id": "x", "chunk_index": 0})
    d2 = Document(page_content="b", metadata={"doc_id": "x", "chunk_index": 1})
    assert chunk_id(d1) != chunk_id(d2)


def test_reingest_does_not_duplicate(sample_txt):
    first = ingest_path(sample_txt)
    assert first["chunks"] > 0
    count_after_first = _row_count()

    second = ingest_path(sample_txt)
    assert second["chunks"] == first["chunks"]
    count_after_second = _row_count()

    assert count_after_second == count_after_first, (
        f"重复入库产生了重复数据：{count_after_first} -> {count_after_second}"
    )


def test_ingest_documents_assigns_ids(sample_txt):
    from langchain_core.documents import Document

    store = get_vectorstore()
    docs = [Document(page_content="唯一内容", metadata={"doc_id": "u1", "chunk_index": 0})]
    store.add_documents(docs, ids=[chunk_id(d) for d in docs])
    assert _row_count() >= 1
