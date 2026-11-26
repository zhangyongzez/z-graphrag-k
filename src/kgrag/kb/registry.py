"""知识库注册表：管理多个知识库（每个库对应一个 Milvus collection）。

持久化到 ``data/registry.json``，结构简单、可直接备份。
并发写通过文件锁 + 原子替换保证。
"""

from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from kgrag.config import get_settings
from kgrag.logging import get_logger

logger = get_logger(__name__)

_lock = threading.RLock()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class KnowledgeBase(BaseModel):
    id: str
    name: str
    description: str = ""
    collection: str
    embedding_dim: int = 1024
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    # 统计（由扫描刷新，不保证实时）
    document_count: int = 0
    chunk_count: int = 0


class _Registry(BaseModel):
    version: int = 1
    kbs: list[KnowledgeBase] = Field(default_factory=list)


class KBRegistry:
    """JSON 文件注册表。"""

    def __init__(self, path: Path | None = None) -> None:
        s = get_settings()
        self.path = path or Path(s.data_dir) / "registry.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: _Registry | None = None

    # ------------------------------------------------------------- 读写
    @contextmanager
    def _locked(self):
        with _lock:
            yield

    def _load(self) -> _Registry:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = _Registry()
            return self._cache
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._cache = _Registry.model_validate(data)
        except Exception as e:  # noqa: BLE001
            logger.error(f"注册表损坏，将重建: {e}")
            self._cache = _Registry()
        return self._cache

    def _save(self) -> None:
        assert self._cache is not None
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._cache.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)  # 原子替换

    # ------------------------------------------------------------- 查询
    def list(self) -> list[KnowledgeBase]:
        with self._locked():
            return list(self._load().kbs)

    def get(self, kb_id: str) -> KnowledgeBase | None:
        with self._locked():
            return next((k for k in self._load().kbs if k.id == kb_id), None)

    def get_by_collection(self, collection: str) -> KnowledgeBase | None:
        with self._locked():
            return next((k for k in self._load().kbs if k.collection == collection), None)

    # ------------------------------------------------------------- 变更
    def create(
        self,
        name: str,
        description: str = "",
        *,
        embedding_dim: int | None = None,
    ) -> KnowledgeBase:
        s = get_settings()
        kb_id = uuid.uuid4().hex[:12]
        # collection 名必须符合 Milvus 命名规则：字母/下划线开头，仅含字母数字下划线
        collection = f"kb_{kb_id}"
        kb = KnowledgeBase(
            id=kb_id,
            name=name,
            description=description,
            collection=collection,
            embedding_dim=embedding_dim or s.embedding_dim,
        )
        with self._locked():
            reg = self._load()
            if any(k.name == name for k in reg.kbs):
                raise ValueError(f"知识库名称已存在: {name}")
            reg.kbs.append(kb)
            self._save()
        logger.info(f"创建知识库: {name} ({kb_id})")
        return kb

    def update(self, kb_id: str, **fields: Any) -> KnowledgeBase:
        allowed = {"name", "description", "document_count", "chunk_count"}
        with self._locked():
            reg = self._load()
            kb = next((k for k in reg.kbs if k.id == kb_id), None)
            if kb is None:
                raise KeyError(f"知识库不存在: {kb_id}")
            for k, v in fields.items():
                if k in allowed:
                    setattr(kb, k, v)
            kb.updated_at = _now()
            self._save()
            return kb

    def delete(self, kb_id: str) -> None:
        with self._locked():
            reg = self._load()
            before = len(reg.kbs)
            reg.kbs = [k for k in reg.kbs if k.id != kb_id]
            if len(reg.kbs) == before:
                raise KeyError(f"知识库不存在: {kb_id}")
            self._save()
        logger.info(f"删除知识库: {kb_id}")


_registry: KBRegistry | None = None


def get_registry() -> KBRegistry:
    global _registry
    if _registry is None:
        _registry = KBRegistry()
    return _registry
