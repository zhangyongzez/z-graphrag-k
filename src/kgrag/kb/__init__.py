"""知识库管理：注册表 + Milvus 集合操作。"""

from kgrag.kb.collections import (
    collection_exists,
    collection_stats,
    delete_document,
    drop_collection,
    reset_client,
)
from kgrag.kb.registry import KBRegistry, KnowledgeBase, get_registry

__all__ = [
    "KBRegistry",
    "KnowledgeBase",
    "collection_exists",
    "collection_stats",
    "delete_document",
    "drop_collection",
    "get_registry",
    "reset_client",
]
