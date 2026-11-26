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
