import pytest

from kgrag.ingest import ingest_path
from kgrag.retrieval import answer, format_context, retrieve
from kgrag.vectorstore import get_vectorstore


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """建一个含少量文档的库，供检索测试复用。"""
    d = tmp_path_factory.mktemp("corpus")
    (d / "milvus.md").write_text(
        "# Milvus\n\nMilvus 是开源的向量数据库，支持 IVF、HNSW 等索引。\n", encoding="utf-8"
    )
    (d / "neo4j.md").write_text(
        "# Neo4j\n\nNeo4j 是图数据库，用 Cypher 查询，适合存实体关系。\n", encoding="utf-8"
    )
    result = ingest_path(d)
    assert result["chunks"] > 0
    return d


def test_vectorstore_created(seeded):
    assert get_vectorstore() is not None


def test_retrieve_returns_docs(seeded):
    docs = retrieve("向量数据库", top_k=2)
    assert len(docs) > 0
    assert all(hasattr(d, "page_content") for d in docs)


def test_retrieve_top_k(seeded):
    docs = retrieve("数据库", top_k=1)
    assert len(docs) <= 1


def test_format_context_empty():
    assert "无相关资料" in format_context([])


def test_format_context_with_docs(seeded):
    docs = retrieve("Milvus", top_k=2)
    ctx = format_context(docs)
    assert "来源" in ctx


def test_answer_with_mock_llm(seeded):
    result = answer("Milvus 是什么？")
    assert "answer" in result
    assert isinstance(result["answer"], str)
    assert result["answer"]
    assert "sources" in result
    assert isinstance(result["sources"], list)


def test_answer_source_shape(seeded):
    result = answer("Neo4j", top_k=1)
    for s in result["sources"]:
        assert "filename" in s
        assert "preview" in s
