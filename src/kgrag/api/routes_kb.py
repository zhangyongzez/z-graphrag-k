"""知识库管理与看板接口（带权限控制）。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from kgrag.api.deps import current_user, require_admin
from kgrag.audit import record as audit
from kgrag.auth.service import User, get_user
from kgrag.ingest import SUPPORTED_SUFFIXES, ingest_path
from kgrag.kb import (
    collection_stats,
    delete_document,
    drop_collection,
    get_registry,
)
from kgrag.kb import permissions as perms
from kgrag.logging import get_logger
from kgrag.settings import effective_settings
from kgrag.vectorstore import reset_vectorstore

logger = get_logger(__name__)
router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])


# --------------------------------------------------------------------------- #
# 模型
# --------------------------------------------------------------------------- #
class KBCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = ""


class KBUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class KBInfo(BaseModel):
    id: str
    name: str
    description: str
    collection: str
    embedding_dim: int
    created_at: str
    updated_at: str
    document_count: int = 0
    chunk_count: int = 0
    owner_id: str | None = None
    owner_name: str = ""
    is_public: bool = False
    is_owner: bool = False
    can_write: bool = False


class DashboardStats(BaseModel):
    kb_count: int
    total_chunks: int
    total_documents: int
    embedding_provider: str
    embedding_dim: int
    vectorstore_provider: str
    llm_provider: str
    llm_model: str


class GrantRequest(BaseModel):
    user_id: str
    can_write: bool = False


class PublicRequest(BaseModel):
    is_public: bool


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _kb_or_404(kb_id: str):
    kb = get_registry().get(kb_id)
    if kb is None:
        raise HTTPException(404, f"知识库不存在: {kb_id}")
    return kb


def _check_read(kb_id: str, user: User) -> None:
    if not perms.can_read(kb_id, user):
        raise HTTPException(403, "无权访问该知识库")


def _check_write(kb_id: str, user: User) -> None:
    if not perms.can_write(kb_id, user):
        raise HTTPException(403, "无权修改该知识库")


def _owner_info(kb_id: str) -> tuple[str | None, str, bool]:
    row = perms.get_owner(kb_id)
    if row is None:
        return None, "", False
    owner_id = row["owner_id"]
    owner_name = ""
    if owner_id:
        u = get_user(owner_id)
        owner_name = u.display_name if u else ""
    return owner_id, owner_name, bool(row["is_public"])


def _to_info(kb, user: User) -> KBInfo:
    stats = collection_stats(kb.collection)
    perm = perms.permission_of(kb.id, user)
    owner_id, owner_name, is_public = _owner_info(kb.id)
    return KBInfo(
        **kb.model_dump(exclude={"document_count", "chunk_count"}),
        document_count=len(stats["documents"]),
        chunk_count=stats["row_count"],
        owner_id=owner_id,
        owner_name=owner_name,
        is_public=is_public,
        is_owner=perm.is_owner,
        can_write=perm.is_owner or perm.can_write,
    )


# --------------------------------------------------------------------------- #
# 知识库 CRUD
# --------------------------------------------------------------------------- #
@router.get("", response_model=list[KBInfo])
def list_kbs(user: User = Depends(current_user)) -> list[KBInfo]:
    visible = perms.visible_kb_ids(user)
    result = []
    for kb in get_registry().list():
        if visible is not None and kb.id not in visible:
            continue
        result.append(_to_info(kb, user))
    return result


@router.post("", response_model=KBInfo, status_code=201)
def create_kb(req: KBCreate, user: User = Depends(current_user)) -> KBInfo:
    try:
        kb = get_registry().create(req.name, req.description)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    # 创建者即为拥有者
    perms.set_owner(kb.id, user.id)
    audit(user.id, user.username, "kb.create", target=kb.name)
    return _to_info(kb, user)


@router.get("/{kb_id}", response_model=KBInfo)
def get_kb(kb_id: str, user: User = Depends(current_user)) -> KBInfo:
    kb = _kb_or_404(kb_id)
    _check_read(kb_id, user)
    return _to_info(kb, user)


@router.patch("/{kb_id}", response_model=KBInfo)
def update_kb(kb_id: str, req: KBUpdate, user: User = Depends(current_user)) -> KBInfo:
    _kb_or_404(kb_id)
    _check_write(kb_id, user)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        kb = get_registry().update(kb_id, **fields)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    audit(user.id, user.username, "kb.update", target=kb_id)
    return _to_info(kb, user)


@router.delete("/{kb_id}", status_code=204)
def delete_kb(kb_id: str, user: User = Depends(current_user)) -> None:
    kb = _kb_or_404(kb_id)
    # 只有拥有者或管理员可删
    perm = perms.permission_of(kb_id, user)
    if not (perm.is_owner or user.is_admin):
        raise HTTPException(403, "只有知识库拥有者可以删除")
    drop_collection(kb.collection)
    get_registry().delete(kb_id)
    perms.delete_kb_permissions(kb_id)
    reset_vectorstore()
    audit(user.id, user.username, "kb.delete", target=kb.name)


# --------------------------------------------------------------------------- #
# 知识库内容
# --------------------------------------------------------------------------- #
@router.get("/{kb_id}/documents")
def list_documents(kb_id: str, user: User = Depends(current_user)) -> dict:
    kb = _kb_or_404(kb_id)
    _check_read(kb_id, user)
    return collection_stats(kb.collection)


@router.delete("/{kb_id}/documents/{filename}", status_code=200)
def remove_document(
    kb_id: str,
    filename: str,
    user: User = Depends(current_user),
) -> dict:
    kb = _kb_or_404(kb_id)
    _check_write(kb_id, user)
    deleted = delete_document(kb.collection, filename)
    if deleted == 0:
        raise HTTPException(404, f"文档不存在: {filename}")
    audit(user.id, user.username, "kb.delete_doc", target=f"{kb_id}/{filename}")
    return {"deleted": deleted}


@router.post("/{kb_id}/upload")
async def upload_files(
    kb_id: str,
    files: Annotated[list[UploadFile], File(...)],
    user: User = Depends(current_user),
) -> dict:
    """上传一个或多个文件到指定知识库。"""
    kb = _kb_or_404(kb_id)
    _check_write(kb_id, user)

    tmpdir = Path(tempfile.mkdtemp())
    results = []
    try:
        for uf in files:
            suffix = Path(uf.filename or "").suffix.lower()
            if suffix not in SUPPORTED_SUFFIXES:
                results.append(
                    {"file": uf.filename, "ok": False, "error": f"不支持的类型: {suffix}"}
                )
                continue
            # 用 Path.name 去掉客户端传来的目录部分，防目录穿越
            safe_name = Path(uf.filename or f"upload{suffix}").name
            target = tmpdir / safe_name
            with target.open("wb") as fh:
                shutil.copyfileobj(uf.file, fh)
            try:
                r = ingest_path(target, collection=kb.collection)
                results.append(
                    {"file": uf.filename, "ok": True, "chunks": r["chunks"]}
                )
            except Exception as e:  # noqa: BLE001
                logger.exception(f"入库失败 {uf.filename}")
                results.append({"file": uf.filename, "ok": False, "error": str(e)})
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    ok = sum(1 for r in results if r["ok"])
    audit(user.id, user.username, "kb.upload", target=kb_id,
          detail=f"ok={ok} fail={len(results) - ok}")
    return {"uploaded": ok, "failed": len(results) - ok, "results": results}


@router.post("/{kb_id}/ingest")
def ingest_local(
    kb_id: str,
    path: str,
    user: User = Depends(current_user),
) -> dict:
    """把服务器本地路径入库到指定知识库。"""
    kb = _kb_or_404(kb_id)
    _check_write(kb_id, user)
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"路径不存在: {path}")
    audit(user.id, user.username, "kb.ingest", target=kb_id, detail=path)
    return ingest_path(p, collection=kb.collection)


# --------------------------------------------------------------------------- #
# 权限管理（管理员）
# --------------------------------------------------------------------------- #
@router.get("/{kb_id}/permissions")
def get_permissions(kb_id: str, admin: User = Depends(require_admin)) -> dict:
    _kb_or_404(kb_id)
    owner_id, owner_name, is_public = _owner_info(kb_id)
    return {
        "owner_id": owner_id,
        "owner_name": owner_name,
        "is_public": is_public,
        "grants": perms.list_grants(kb_id),
    }


@router.post("/{kb_id}/permissions", status_code=201)
def grant_permission(
    kb_id: str,
    req: GrantRequest,
    admin: User = Depends(require_admin),
) -> dict:
    _kb_or_404(kb_id)
    if get_user(req.user_id) is None:
        raise HTTPException(404, "用户不存在")
    perms.grant(kb_id, req.user_id, can_write=req.can_write)
    audit(admin.id, admin.username, "kb.grant", target=kb_id, detail=req.user_id)
    return {"ok": True}


@router.delete("/{kb_id}/permissions/{user_id}", status_code=204)
def revoke_permission(
    kb_id: str,
    user_id: str,
    admin: User = Depends(require_admin),
) -> None:
    _kb_or_404(kb_id)
    perms.revoke(kb_id, user_id)
    audit(admin.id, admin.username, "kb.revoke", target=kb_id, detail=user_id)


@router.put("/{kb_id}/public")
def set_public(
    kb_id: str,
    req: PublicRequest,
    admin: User = Depends(require_admin),
) -> dict:
    _kb_or_404(kb_id)
    row = perms.get_owner(kb_id)
    owner_id = row["owner_id"] if row else None
    perms.set_owner(kb_id, owner_id, is_public=req.is_public)
    audit(admin.id, admin.username, "kb.public", target=kb_id, detail=str(req.is_public))
    return {"ok": True, "is_public": req.is_public}


# --------------------------------------------------------------------------- #
# 看板
# --------------------------------------------------------------------------- #
@router.get("/stats/dashboard", response_model=DashboardStats)
def dashboard(user: User = Depends(current_user)) -> DashboardStats:
    s = effective_settings()
    reg = get_registry()
    visible = perms.visible_kb_ids(user)

    total_chunks = 0
    docs: set[str] = set()
    kbs = [kb for kb in reg.list() if visible is None or kb.id in visible]
    for kb in kbs:
        stats = collection_stats(kb.collection)
        total_chunks += stats["row_count"]
        docs.update(d["filename"] for d in stats["documents"])

    # 必须反映「系统配置里选中的模型」，否则看板和侧边栏会显示不一致
    from kgrag.settings import selected_model

    llm_rec = selected_model("llm")
    emb_rec = selected_model("embedding")

    return DashboardStats(
        kb_count=len(kbs),
        total_chunks=total_chunks,
        total_documents=len(docs),
        embedding_provider=(emb_rec or {}).get("provider") or s.embedding_provider,
        embedding_dim=(emb_rec or {}).get("dim") or s.embedding_dim,
        vectorstore_provider=s.vectorstore_provider,
        llm_provider=(llm_rec or {}).get("provider") or s.llm_provider,
        llm_model=(llm_rec or {}).get("model") or s.active_llm_model,
    )
