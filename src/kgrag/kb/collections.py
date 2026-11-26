"""知识库级操作：按 collection 建/删、统计、文档清单。

.. important::

    **Milvus Lite 只能有一个连接**

    milvus-lite 在进程内起一个本地 gRPC 服务。每次 ``MilvusClient(...)``
    都会尝试重建/接管这个服务，和 langchain-milvus 持有的连接互相踢下线
    （表现为 ``Too many pings`` / ``Fail connecting to server``）。
    因此这里所有只读/维护操作共用一个惰性单例客户端，绝不 ``close()``。
"""

from __future__ import annotations

import json
import threading
from collections import Counter

from pymilvus import MilvusClient

from kgrag.config import Settings, get_settings
from kgrag.logging import get_logger

logger = get_logger(__name__)

_lock = threading.Lock()
_shared: MilvusClient | None = None


def _client(s: Settings | None = None) -> MilvusClient:
    """返回可用的 Milvus 客户端。

    **Milvus Lite 模式必须复用 langchain 的客户端**：milvus-lite 在进程内
    只允许一个本地服务实例，第二个 ``MilvusClient(同一路径)`` 会失败
    （``Open local milvus failed`` / ``Too many pings``）。因此先尝试从
    langchain 的 VectorStore 里取它已经建好的客户端。

    服务端模式（``milvus``）允许多连接，可以自己建一个。
    """
    global _shared
    if _shared is not None:
        return _shared
    with _lock:
        if _shared is not None:
            return _shared
        s = s or get_settings()

        if s.vectorstore_provider == "lite":
            # 优先复用 langchain 已建立的连接
            try:
                from kgrag.vectorstore import get_vectorstore

                store = get_vectorstore(s.vectorstore_collection)
                _shared = store.client
                return _shared
            except Exception as e:  # noqa: BLE001 - 还没有 store 时自己建
                logger.debug(f"复用 langchain 客户端失败，尝试自建: {e}")
            _shared = MilvusClient(s.vectorstore_lite_path)
        else:
            kwargs: dict = {"uri": s.milvus_uri}
            if s.milvus_token:
                kwargs["token"] = s.milvus_token
            if s.milvus_db_name:
                kwargs["db_name"] = s.milvus_db_name
            _shared = MilvusClient(**kwargs)
        return _shared


def reset_client() -> None:
    """切换配置或测试时清掉单例（不要主动 close，交给进程退出）。"""
    global _shared
    with _lock:
        _shared = None


def collection_exists(collection: str) -> bool:
    try:
        return _client().has_collection(collection)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"检查集合失败 {collection}: {e}")
        return False


def _iter_rows(client: MilvusClient, collection: str, fields: list[str]) -> list[dict]:
    """遍历集合全部行。

    主键 ``pk`` 是字符串，不能用 ``pk > 0`` 之类的数值比较；
    用 ``query_iterator`` 以空表达式遍历，内部分页，避免 limit 上限。
    """
    it = client.query_iterator(
        collection_name=collection,
        filter="",
        output_fields=fields,
        batch_size=1000,
    )
    rows: list[dict] = []
    try:
        while True:
            batch = it.next()
            if not batch:
                break
            rows.extend(batch)
    finally:
        it.close()
    return rows


def _row_filename(row: dict) -> str | None:
    """从一行里取出文件名。

    LangChain 在 ``enable_dynamic_field=True`` 时会把 metadata 拍平成顶层
    动态字段（``filename`` / ``source`` 直接是行的一级键）；同时也兼容
    存在 ``metadata`` 子字段的情况。
    """
    if row.get("filename"):
        return str(row["filename"])
    if row.get("source"):
        return str(row["source"])
    meta = row.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            return None
    if isinstance(meta, dict):
        return meta.get("filename") or meta.get("source")
    return None


def collection_stats(collection: str) -> dict:
    """返回 ``{"row_count": int, "documents": [{"filename", "chunks"}...]}``。"""
    try:
        client = _client()
        if not client.has_collection(collection):
            return {"row_count": 0, "documents": []}
        client.load_collection(collection)
        rows = _iter_rows(client, collection, ["filename", "source"])
    except Exception as e:  # noqa: BLE001 - 空集合或未加载
        logger.debug(f"读取集合统计失败 {collection}: {e}")
        return {"row_count": 0, "documents": []}

    # 不用 get_collection_stats：Milvus 服务端刚写入时该统计基于已落盘的段，
    # 有延迟会返回 0。以实际遍历到的行数为准。
    row_count = len(rows)

    counter: Counter[str] = Counter()
    for r in rows:
        counter[_row_filename(r) or "未知"] += 1

    documents = [
        {"filename": name, "chunks": count}
        for name, count in sorted(counter.items(), key=lambda x: -x[1])
    ]
    return {"row_count": row_count, "documents": documents}


def drop_collection(collection: str) -> None:
    client = _client()
    if client.has_collection(collection):
        client.drop_collection(collection)
        logger.info(f"已删除集合: {collection}")


def delete_document(collection: str, filename: str) -> int:
    """按文件名删除该文档的所有块，返回删除条数。"""
    client = _client()
    if not client.has_collection(collection):
        return 0
    client.load_collection(collection)
    rows = _iter_rows(client, collection, ["pk", "filename", "source"])
    pks = [str(r["pk"]) for r in rows if _row_filename(r) == filename]
    if pks:
        client.delete(collection_name=collection, ids=pks)
        logger.info(f"从 {collection} 删除 {filename}: {len(pks)} 块")
    return len(pks)
