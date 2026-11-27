import pytest

from kgrag.config import Settings, get_settings


def test_defaults():
    s = get_settings()
    assert s.app_name
    assert s.embedding_provider in {"local", "openai", "ollama", "hash"}
    assert s.vectorstore_provider in {"lite", "milvus"}


def test_relative_paths_resolved_to_project_root():
    s = Settings(_env_file=None, data_dir="./data", upload_dir="./data/uploads")
    from pathlib import Path

    assert Path(s.data_dir).is_absolute()
    assert Path(s.upload_dir).is_absolute()


def test_milvus_connection_uri_lite():
    s = Settings(_env_file=None, vectorstore_provider="lite", vectorstore_lite_path="./x.db")
    assert s.milvus_connection_uri.endswith("x.db")


def test_milvus_connection_uri_server():
    s = Settings(
        _env_file=None,
        vectorstore_provider="milvus",
        milvus_uri="http://example:19530",
    )
    assert s.milvus_connection_uri == "http://example:19530"


def test_embedding_source_falls_back_to_model_name(tmp_path):
    s = Settings(
        _env_file=None,
        embedding_provider="local",
        embedding_model="BAAI/bge-m3",
        embedding_local_path=str(tmp_path / "not-exist"),
    )
    assert s.embedding_source == "BAAI/bge-m3"


def test_embedding_source_prefers_local_dir(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    s = Settings(
        _env_file=None,
        embedding_provider="local",
        embedding_model="BAAI/bge-m3",
        embedding_local_path=str(model_dir),
    )
    assert s.embedding_source == str(model_dir.resolve())


def test_invalid_provider_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, vectorstore_provider="not-a-provider")


def test_extract_text_handles_content_blocks():
    """部分模型返回 thinking + text 内容块，应只取正文。"""
    from kgrag.retrieval.rag import _extract_text

    # 纯字符串
    assert _extract_text("你好") == "你好"
    # 内容块列表（DeepSeek / Claude thinking 模式）
    blocks = [
        {"type": "thinking", "thinking": "推理过程"},
        {"type": "text", "text": "真正的回答"},
    ]
    assert _extract_text(blocks) == "真正的回答"
    # 多个 text 块
    assert _extract_text([{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]) == "A\nB"
    # 空列表
    assert _extract_text([]) == ""


def test_extract_reasoning():
    """thinking 块应被单独取出。"""
    from kgrag.retrieval.rag import _extract_reasoning

    blocks = [
        {"type": "thinking", "thinking": "让我想想"},
        {"type": "text", "text": "回答"},
    ]
    assert _extract_reasoning(blocks) == "让我想想"
    assert _extract_reasoning("纯文本") == ""
    assert _extract_reasoning([]) == ""
    # 没有 thinking 块
    assert _extract_reasoning([{"type": "text", "text": "x"}]) == ""


def test_extract_text_ignores_thinking():
    """正文提取应排除 thinking。"""
    from kgrag.retrieval.rag import _extract_text

    blocks = [
        {"type": "thinking", "thinking": "推理"},
        {"type": "text", "text": "回答"},
    ]
    assert _extract_text(blocks) == "回答"
    assert "推理" not in _extract_text(blocks)
