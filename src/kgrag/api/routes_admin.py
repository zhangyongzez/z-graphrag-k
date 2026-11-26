"""管理员接口：配置页、模型管理、系统看板、审计日志。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from kgrag import models as model_store
from kgrag.api.deps import require_admin
from kgrag.audit import recent as recent_audit
from kgrag.audit import record as audit
from kgrag.auth.service import User
from kgrag.config import get_settings
from kgrag.kb import collection_stats, get_registry
from kgrag.logging import get_logger
from kgrag.settings import (
    DEFAULT_CONFIG,
    RESTART_REQUIRED,
    config_schema,
    public_config,
    reset_config,
    selected_model,
    update_config,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


class ConfigUpdate(BaseModel):
    values: dict


class ModelCreate(BaseModel):
    name: str
    kind: str  # llm | embedding
    provider: str
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    path: str = ""
    dim: int | None = None
    note: str = ""


class ModelUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    path: str | None = None
    dim: int | None = None
    note: str | None = None


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
@router.get("/config")
def get_config(admin: User = Depends(require_admin)) -> dict:
    """配置页表单结构 + 当前值 + 默认值。"""
    return {
        "groups": config_schema(),
        "current": public_config(),
        "defaults": DEFAULT_CONFIG,
        "restart_required": sorted(RESTART_REQUIRED),
    }


@router.put("/config")
def put_config(req: ConfigUpdate, admin: User = Depends(require_admin)) -> dict:
    result = update_config(req.values, updated_by=admin.username)
    audit(admin.id, admin.username, "config.update", detail=",".join(result["changed"]))
    return result


@router.post("/config/reset")
def reset_config_route(admin: User = Depends(require_admin)) -> dict:
    """恢复默认配置。"""
    result = reset_config(updated_by=admin.username)
    audit(admin.id, admin.username, "config.reset")
    return result


# --------------------------------------------------------------------------- #
# 模型管理
# --------------------------------------------------------------------------- #
@router.get("/models")
def list_models(
    kind: str | None = None,
    admin: User = Depends(require_admin),
) -> list[dict]:
    """列出所有模型，可按 kind（llm / embedding）过滤。"""
    return model_store.list_models(kind)


@router.get("/models/providers")
def list_providers(admin: User = Depends(require_admin)) -> dict:
    """每类模型支持的提供方，供新增表单下拉。"""
    return model_store.PROVIDERS


@router.post("/models/inspect")
def inspect_model(path: str, admin: User = Depends(require_admin)) -> dict:
    """检查一个路径是不是有效的本地模型目录。"""
    return model_store.inspect_local(path)


@router.post("/models", status_code=201)
def create_model(req: ModelCreate, admin: User = Depends(require_admin)) -> dict:
    try:
        m = model_store.create_model(
            name=req.name,
            kind=req.kind,
            provider=req.provider,
            model=req.model,
            base_url=req.base_url,
            api_key=req.api_key,
            path=req.path,
            dim=req.dim,
            note=req.note,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    audit(admin.id, admin.username, "model.create", target=req.name,
          detail=f"{req.kind}/{req.provider}")
    return m


@router.patch("/models/{model_id}")
def update_model(
    model_id: str,
    req: ModelUpdate,
    admin: User = Depends(require_admin),
) -> dict:
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        m = model_store.update_model(model_id, **fields)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    audit(admin.id, admin.username, "model.update", target=model_id)
    return m


@router.post("/models/import", status_code=201)
def import_model_dir(
    src: str,
    name: str = "",
    admin: User = Depends(require_admin),
) -> dict:
    """把服务器上的模型目录复制进 models/ 并返回路径（不自动登记）。"""
    try:
        path = model_store.import_directory(src, name=name)
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e)) from e
    audit(admin.id, admin.username, "model.import", target=src)
    return {"path": path}


@router.post("/models/upload")
async def upload_model_files(
    files: Annotated[list[UploadFile], File(...)],
    subdir: str = "",
    admin: User = Depends(require_admin),
) -> dict:
    """上传模型文件到 ``models/<subdir>/``（大模型目录需分多次上传）。"""
    saved = []
    for uf in files:
        rel = f"{subdir}/{uf.filename}" if subdir else (uf.filename or "model.bin")
        data = await uf.read()
        try:
            saved.append(model_store.save_upload(rel, data))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    return {"saved": len(saved), "files": saved}


@router.delete("/models/{model_id}", status_code=204)
def delete_model(
    model_id: str,
    remove_files: bool = False,
    admin: User = Depends(require_admin),
) -> None:
    try:
        model_store.delete_model(model_id, remove_files=remove_files)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    audit(admin.id, admin.username, "model.delete", target=model_id)


# --------------------------------------------------------------------------- #
# 系统看板
# --------------------------------------------------------------------------- #
def _check_service(url: str, timeout: float = 1.0) -> str:
    """探测服务健康：ok / err / unknown。"""
    if not url:
        return "unknown"
    try:
        import httpx

        r = httpx.get(url, timeout=timeout, follow_redirects=True)
        return "ok" if r.status_code < 500 else "err"
    except Exception:  # noqa: BLE001
        return "err"


def _check_services(urls: dict[str, str], timeout: float = 1.0) -> dict[str, str]:
    """并发探测多个服务，避免串行等待把看板拖慢到十几秒。"""
    if not urls:
        return {}
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
        futures = {name: pool.submit(_check_service, url, timeout) for name, url in urls.items()}
        return {name: f.result() for name, f in futures.items()}


@router.get("/dashboard")
def admin_dashboard(admin: User = Depends(require_admin)) -> dict:
    """管理员看板：模型配置、服务地址、知识库全貌。"""
    s = get_settings()
    reg = get_registry()

    kbs = []
    for kb in reg.list():
        stats = collection_stats(kb.collection)
        kbs.append(
            {
                "id": kb.id,
                "name": kb.name,
                "description": kb.description,
                "collection": kb.collection,
                "embedding_dim": kb.embedding_dim,
                "document_count": len(stats["documents"]),
                "chunk_count": stats["row_count"],
                "created_at": kb.created_at,
            }
        )

    vectorstore_uri = s.milvus_connection_uri
    llm_rec = selected_model("llm")
    emb_rec = selected_model("embedding")
    llm_addr = (llm_rec or {}).get("base_url") or s.ollama_base_url or "—"
    emb_addr = (emb_rec or {}).get("base_url") or (emb_rec or {}).get("path") or "—"

    # 并发探测，避免串行超时把看板拖到十几秒
    checks = _check_services(
        {
            "vectorstore": (
                f"http://{s.milvus_uri.split('//')[-1]}/healthz"
                if s.vectorstore_provider == "milvus"
                else ""
            ),
            "llm": (
                (llm_rec or {}).get("base_url", "")
                if (llm_rec or {}).get("provider") in ("openai", "ollama")
                else ""
            ),
            "ollama": s.ollama_base_url,
        }
    )

    services = [
        {
            "name": "API 服务",
            "address": f"http://{s.app_host}:{s.app_port}",
            "status": "ok",
        },
        {
            "name": "向量库",
            "address": vectorstore_uri,
            "status": checks.get("vectorstore", "unknown"),
        },
        {
            "name": "大模型",
            "address": llm_addr,
            "status": checks.get("llm", "unknown"),
        },
        {
            "name": "Ollama",
            "address": s.ollama_base_url,
            "status": checks.get("ollama", "unknown"),
        },
    ]

    return {
        "models": {
            "embedding": {
                "selected": emb_rec["name"] if emb_rec else None,
                "provider": (emb_rec or {}).get("provider", s.embedding_provider),
                "model": (emb_rec or {}).get("model") or s.embedding_model,
                "device": s.embedding_device,
                "dim": (emb_rec or {}).get("dim") or s.embedding_dim,
                "address": emb_addr,
            },
            "llm": {
                "selected": llm_rec["name"] if llm_rec else None,
                "provider": (llm_rec or {}).get("provider", s.llm_provider),
                "model": (llm_rec or {}).get("model") or s.active_llm_model,
                "address": llm_addr,
            },
            "vectorstore": {
                "provider": s.vectorstore_provider,
                "uri": vectorstore_uri,
                "collection": s.vectorstore_collection,
            },
            "registry": model_store.list_models(),
        },
        "services": services,
        "knowledge_bases": kbs,
        "totals": {
            "kb_count": len(kbs),
            "documents": sum(k["document_count"] for k in kbs),
            "chunks": sum(k["chunk_count"] for k in kbs),
        },
    }


@router.get("/audit")
def audit_log(limit: int = 100, admin: User = Depends(require_admin)) -> list[dict]:
    return recent_audit(min(limit, 500))


@router.get("/paths")
def server_paths(admin: User = Depends(require_admin)) -> dict:
    """服务器关键路径，便于排障。"""
    s = get_settings()
    return {
        "data_dir": s.data_dir,
        "upload_dir": s.upload_dir,
        "models_dir": str(model_store.models_root()),
        "vectorstore_lite_path": s.vectorstore_lite_path,
        "project_root": str(Path(s.data_dir).parent),
    }
