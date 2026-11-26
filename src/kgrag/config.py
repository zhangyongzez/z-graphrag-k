"""统一配置：所有服务通过环境变量 / .env 管理。

设计原则
--------
1. 每种能力（向量库 / Embedding / LLM / 图库）用 ``provider`` 字段选择实现，
   切换实现只改环境变量，不改代码。
2. 配置在进程内单例缓存，测试时调用 :func:`get_settings.cache_clear()` 重置。
3. 所有相对路径统一解析到项目根目录，避免受 cwd 影响。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- 应用
    app_name: str = "z-graphrag-k"
    app_env: Literal["dev", "test", "prod"] = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    # ------------------------------------------------------------ 向量库
    vectorstore_provider: Literal["lite", "milvus"] = "lite"
    vectorstore_collection: str = "kgrag_chunks"
    vectorstore_metric: str = "IP"
    vectorstore_index_type: str = "AUTOINDEX"

    vectorstore_lite_path: str = "./data/milvus/kgrag.db"

    milvus_uri: str = "http://localhost:19530"
    milvus_token: str = ""
    milvus_db_name: str = "default"

    # ---------------------------------------------------------- Embedding
    embedding_provider: Literal["local", "openai", "ollama", "hash"] = "local"
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: str = "auto"
    embedding_dim: int = 1024
    embedding_batch_size: int = 32
    embedding_max_length: int = 1024
    embedding_normalize: bool = True

    embedding_local_path: str = "./models/bge-m3"

    embedding_api_base: str = ""
    embedding_api_key: str = ""
    embedding_api_model: str = "text-embedding-3-small"

    ollama_base_url: str = "http://localhost:11434"

    # ---------------------------------------------------------------- LLM
    # 默认走 Claude；LLM_PROVIDER=openai 可切到任意 OpenAI 兼容端点
    llm_provider: Literal["openai", "anthropic", "ollama", "mock"] = "anthropic"
    llm_model: str = ""
    llm_temperature: float = 0.2
    llm_max_tokens: int = 2048
    llm_timeout: int = 60

    # OpenAI 兼容端点（DeepSeek / vLLM / 公司网关等）
    llm_api_base: str = ""
    llm_api_key: str = ""
    llm_api_model: str = "gpt-4o-mini"

    # Anthropic / Claude
    anthropic_api_base: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    ollama_model: str = "qwen2.5:7b"

    # ---------------------------------------------------------------- 图库
    graph_enabled: bool = False
    neo4j_uri: str = "bolt://localhost:7688"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "please_change_me"
    neo4j_database: str = "neo4j"

    # ------------------------------------------------------------ 切分/检索
    chunk_size: int = 800
    chunk_overlap: int = 120
    retrieval_top_k: int = 5
    retrieval_score_threshold: float = 0.0

    # ---------------------------------------------------------------- AD 域
    # 管理员在配置页保存后写入 SQLite，覆盖这里的默认值
    ad_enabled: bool = False
    ad_server_uri: str = ""       # ldap://ad.example.com:389
    ad_use_ssl: bool = False
    ad_base_dn: str = ""          # dc=example,dc=com
    ad_user_filter: str = "(sAMAccountName={username})"
    ad_bind_dn: str = ""          # 服务账号
    ad_bind_password: str = ""
    ad_display_name_attr: str = "displayName"
    ad_email_attr: str = "mail"

    # ---------------------------------------------------------------- 数据
    data_dir: str = "./data"
    upload_dir: str = "./data/uploads"

    # ------------------------------------------------------------- 解析
    @field_validator(
        "vectorstore_lite_path",
        "embedding_local_path",
        "data_dir",
        "upload_dir",
    )
    @classmethod
    def _resolve_path(cls, v: str) -> str:
        """相对路径统一解析到项目根，绝对路径原样返回。"""
        if not v:
            return v
        p = Path(v)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return str(p.resolve())

    @model_validator(mode="after")
    def _ensure_dirs(self) -> Settings:
        for d in (self.data_dir, self.upload_dir):
            Path(d).mkdir(parents=True, exist_ok=True)
        if self.vectorstore_provider == "lite":
            Path(self.vectorstore_lite_path).parent.mkdir(parents=True, exist_ok=True)
        return self

    # ------------------------------------------------------------- 派生
    @property
    def milvus_connection_uri(self) -> str:
        """Milvus 连接串：lite 模式下返回本地文件路径。"""
        if self.vectorstore_provider == "lite":
            return self.vectorstore_lite_path
        return self.milvus_uri

    @property
    def embedding_source(self) -> str:
        """Embedding 模型加载来源：优先本地目录，否则用模型名。"""
        if self.embedding_provider == "local":
            p = Path(self.embedding_local_path)
            if p.is_dir():
                return str(p)
        return self.embedding_model

    @property
    def active_llm_model(self) -> str:
        """当前 provider 实际使用的模型名（LLM_MODEL 优先）。"""
        if self.llm_model:
            return self.llm_model
        return {
            "anthropic": self.anthropic_model,
            "openai": self.llm_api_model,
            "ollama": self.ollama_model,
        }.get(self.llm_provider, "")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # HF 相关变量必须在导入 transformers 之前生效，这里兜底写入 os.environ
    if os.getenv("HF_ENDPOINT"):
        os.environ.setdefault("HF_ENDPOINT", os.environ["HF_ENDPOINT"])
    return Settings()


settings = get_settings()
