
"""pytest 全局配置：所有测试默认用 hash embedding + mock LLM，不依赖模型和网络。"""

import os
import tempfile
from pathlib import Path

import pytest

# 必须在导入 kgrag 之前设置，config 会在导入时读取
_TMP = Path(tempfile.mkdtemp(prefix="kgrag_test_"))
os.environ.update(
    {
        "APP_ENV": "test",
        "EMBEDDING_PROVIDER": "hash",
        "EMBEDDING_DIM": "256",
        "LLM_PROVIDER": "mock",
        "VECTORSTORE_PROVIDER": "lite",
        "VECTORSTORE_LITE_PATH": str(_TMP / "milvus" / "test.db"),
        "VECTORSTORE_COLLECTION": "test_chunks",
        "DATA_DIR": str(_TMP / "data"),
        "UPLOAD_DIR": str(_TMP / "uploads"),
        "CHUNK_SIZE": "200",
        "CHUNK_OVERLAP": "20",
    }
)


@pytest.fixture(scope="session")
def tmp_root() -> Path:
    return _TMP


@pytest.fixture
def settings():
    from kgrag.config import get_settings

    return get_settings()


@pytest.fixture(autouse=True)
def _clear_caches():
    """每个测试前清掉单例，避免相互污染。"""
    yield
    from kgrag.embeddings import reset_embeddings
    from kgrag.llm import reset_llm
    from kgrag.vectorstore import reset_vectorstore

    reset_embeddings()
    reset_llm()
    reset_vectorstore()


@pytest.fixture
def sample_md(tmp_path: Path) -> Path:
    p = tmp_path / "sample.md"
    p.write_text(
        "# 产品手册\n\n"
        "## 安装\n\n"
        "使用 pip 安装本工具，需要 Python 3.11 以上版本。\n\n"
        "## 配置\n\n"
        "所有配置项都可以通过环境变量设置，也可以写在 .env 文件里。\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def sample_txt(tmp_path: Path) -> Path:
    p = tmp_path / "notes.txt"
    p.write_text(
        "Milvus 是一个开源的向量数据库，支持多种索引类型。\n"
        "Neo4j 是一个图数据库，适合存储实体关系。\n",
        encoding="utf-8",
    )
    return p
