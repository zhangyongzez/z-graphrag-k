"""
向量库工厂：按 ``VECTORSTORE_PROVIDER`` 返回 LangChain ``VectorStore``。

- ``lite``   : Milvus Lite（本地文件，零依赖，开发首选）
- ``milvus`` : 独立 Milvus 服务（生产）

统一使用 LangChain 的 ``Milvus`` 封装，保证两种模式 API 完全一致。
支持按 collection 名获取不同知识库的向量库实例。
"""

from __future__ import annotations

from functools import lru_cache

from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore
from langchain_milvus import Milvus

from kgrag.config import Settings, get_settings
from kgrag.embeddings import get_embeddings
from kgrag.logging import get_logger

logger = get_logger(__name__)


def _connection_args(s: Settings) -> dict:
    args: dict = {"uri": s.milvus_connection_uri}
    if s.vectorstore_provider == "milvus":
        if s.milvus_token:
            args["token"] = s.milvus_token
        if s.milvus_db_name:
            args["db_name"] = s.milvus_db_name
    return args


def _build_store(
    s: Settings,
    embeddings: Embeddings,
    collection: str,
) -> Milvus:
    conn = _connection_args(s)
    logger.info(
        f"连接向量库: provider={s.vectorstore_provider} "
        f"collection={collection} uri={conn['uri']}"
    )
    return Milvus(
        embedding_function=embeddings,
        collection_name=collection,
        connection_args=conn,
        index_params={
            "metric_type": s.vectorstore_metric,
            "index_type": s.vectorstore_index_type,
        },
        # auto_id=False：由我们传入稳定 ID，同一文件重复入库可原地覆盖
        auto_id=False,
        drop_old=False,
        text_field="text",
        vector_field="vector",
        primary_field="pk",
        metadata_field="metadata",
        enable_dynamic_field=True,
    )


@lru_cache(maxsize=16)
def _store_for(collection: str) -> VectorStore:
    from kgrag.settings import effective_settings

    s = effective_settings()
    return _build_store(s, get_embeddings(), collection)


def get_vectorstore(collection: str | None = None) -> VectorStore:
    """按 collection 名获取向量库；默认取配置里的默认集合。"""
    s = get_settings()
    return _store_for(collection or s.vectorstore_collection)


def reset_vectorstore() -> None:
    """清掉 langchain 的 VectorStore 缓存。

    .. important::

        **不要同时清掉共享的 MilvusClient。**

        milvus-lite 在进程内起一个本地服务并持有文件锁。如果这里把
        ``_shared`` 置空，下次会新建 ``MilvusClient(同一路径)``，
        而旧服务仍占着锁，就会抛 ``Open local milvus failed``。
        共享客户端由 ``kb.collections`` 持有，只在切换向量库配置时
        才需要重置（见 ``settings.update_config``）。
    """
    _store_for.cache_clear()


def get_retriever(
    *,
    top_k: int | None = None,
    score_threshold: float | None = None,
    filter_expr: str | None = None,
    collection: str | None = None,
):
    """构造检索器。``filter_expr`` 为 Milvus 表达式，用于权限过滤。"""
    from kgrag.settings import effective_settings

    s = effective_settings()
    k = top_k or s.retrieval_top_k
    threshold = s.retrieval_score_threshold if score_threshold is None else score_threshold

    search_kwargs: dict = {"k": k}
    if filter_expr:
        search_kwargs["expr"] = filter_expr

    store = get_vectorstore(collection)
    if threshold > 0:
        return store.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={"score_threshold": threshold, **search_kwargs},
        )
    return store.as_retriever(search_kwargs=search_kwargs)
