import math

import pytest

from kgrag.embeddings import HashEmbeddings, get_embeddings


def test_hash_embeddings_dim():
    e = HashEmbeddings(dim=128)
    v = e.embed_query("hello")
    assert len(v) == 128


def test_hash_embeddings_deterministic():
    e = HashEmbeddings(dim=128)
    assert e.embed_query("相同文本") == e.embed_query("相同文本")


def test_hash_embeddings_different_for_different_text():
    e = HashEmbeddings(dim=128)
    assert e.embed_query("文本甲") != e.embed_query("文本乙")


def test_hash_embeddings_normalized():
    e = HashEmbeddings(dim=128, normalize=True)
    v = e.embed_query("归一化测试")
    norm = math.sqrt(sum(x * x for x in v))
    assert norm == pytest.approx(1.0, abs=1e-6)


def test_embed_documents_batch():
    e = HashEmbeddings(dim=64)
    vecs = e.embed_documents(["a", "b", "c"])
    assert len(vecs) == 3
    assert all(len(v) == 64 for v in vecs)


def test_get_embeddings_returns_singleton():
    assert get_embeddings() is get_embeddings()
