"""
FastAPI 应用入口。

启动：``python start.py`` / ``uv run kgrag serve`` /
``uvicorn kgrag.api.app:app --reload``

- ``/``              登录页 / 管理页面
- ``/api/auth/*``    登录、用户管理
- ``/api/admin/*``   配置、模型管理、系统看板、审计
- ``/api/kb/*``      知识库管理
- ``/api/chat/*``    会话与对话
- ``/api/*``         兼容旧接口（health / search / ask / ingest）
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from kgrag import __version__
from kgrag.api.deps import current_user
from kgrag.api.routes_admin import router as admin_router
from kgrag.api.routes_auth import router as auth_router
from kgrag.api.routes_chat import router as chat_router
from kgrag.api.routes_kb import router as kb_router
from kgrag.auth.service import User, cleanup_sessions, ensure_admin
from kgrag.config import get_settings
from kgrag.db import init_db
from kgrag.ingest import ingest_path, iter_files
from kgrag.logging import get_logger
from kgrag.retrieval import answer as rag_answer
from kgrag.retrieval import retrieve

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from kgrag import models as model_store

    init_db()
    ensure_admin()
    cleanup_sessions()
    model_store.migrate_legacy()
    logger.info(f"z-graphrag-k {__version__} 启动完成")
    yield


app = FastAPI(
    title="z-graphrag-k",
    version=__version__,
    description="Milvus + LangChain + Neo4j 可插拔知识库",
    lifespan=lifespan,
)

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(kb_router)
app.include_router(chat_router)


# --------------------------------------------------------------------------- #
# 模型
# --------------------------------------------------------------------------- #
class HealthResponse(BaseModel):
    status: str
    version: str
    vectorstore: str
    embedding: str
    llm: str
    llm_model: str
    # 当前是否已在「模型管理」中登记并选中模型；false 表示回退到 .env 默认值
    embedding_ready: bool = False
    llm_ready: bool = False
    # 「系统配置」里选中的 LLM 记录 id，供对话页下拉框预选
    llm_model_id: str = ""


class IngestRequest(BaseModel):
    path: str = Field(..., description="服务器本地文件或目录路径")
    tags: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str
    top_k: int | None = None
    filter_expr: str | None = Field(None, description="Milvus 过滤表达式")
    kb_id: str | None = None


class AskRequest(BaseModel):
    question: str
    top_k: int | None = None
    filter_expr: str | None = None
    history: list[tuple[str, str]] = Field(default_factory=list)
    kb_id: str | None = None


class Source(BaseModel):
    filename: str | None = None
    source: str | None = None
    chunk_index: int | None = None
    score: float | None = None
    preview: str | None = None


class SearchResponse(BaseModel):
    results: list[Source]


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]


# --------------------------------------------------------------------------- #
# 系统
# --------------------------------------------------------------------------- #
@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    from kgrag.settings import selected_model

    s = get_settings()
    llm_rec = selected_model("llm")
    emb_rec = selected_model("embedding")
    return HealthResponse(
        status="ok",
        version=__version__,
        vectorstore=s.vectorstore_provider,
        embedding=(emb_rec or {}).get("provider") or s.embedding_provider,
        llm=(llm_rec or {}).get("provider") or s.llm_provider,
        llm_model=(llm_rec or {}).get("model") or s.active_llm_model,
        embedding_ready=emb_rec is not None,
        llm_ready=llm_rec is not None,
        llm_model_id=(llm_rec or {}).get("id", ""),
    )


@app.get("/api/models", tags=["models"])
def list_available_models(kind: str | None = None) -> list[dict]:
    """登录用户可选的模型清单（会话里选大模型用）。

    只返回非敏感字段，不含 api_key / base_url。
    """
    from kgrag import models as model_store

    return model_store.list_models(kind)


@app.get("/config", tags=["system"])
def show_config() -> dict:
    """脱敏后的运行配置，便于排障。"""
    s = get_settings().model_dump()
    for k in list(s):
        if any(t in k for t in ("key", "password", "token")):
            s[k] = "***" if s[k] else ""
    return s


# --------------------------------------------------------------------------- #
# 兼容旧接口（需登录）
# --------------------------------------------------------------------------- #
def _collection_of(kb_id: str | None) -> str | None:
    if not kb_id:
        return None
    from fastapi import HTTPException

    from kgrag.kb import get_registry

    kb = get_registry().get(kb_id)
    if kb is None:
        raise HTTPException(404, f"知识库不存在: {kb_id}")
    return kb.collection


@app.post("/ingest", tags=["ingest"])
def ingest(req: IngestRequest, user: User = Depends(current_user)) -> dict:
    from fastapi import HTTPException

    if not Path(req.path).exists():
        raise HTTPException(404, f"路径不存在: {req.path}")
    return ingest_path(req.path)


@app.post("/search", response_model=SearchResponse, tags=["query"])
def search(req: SearchRequest, user: User = Depends(current_user)) -> SearchResponse:
    docs = retrieve(
        req.query,
        top_k=req.top_k,
        filter_expr=req.filter_expr,
        collection=_collection_of(req.kb_id),
    )
    return SearchResponse(
        results=[
            Source(
                filename=d.metadata.get("filename"),
                source=d.metadata.get("source"),
                chunk_index=d.metadata.get("chunk_index"),
                score=d.metadata.get("score"),
                preview=d.page_content[:300],
            )
            for d in docs
        ]
    )


@app.post("/ask", response_model=AskResponse, tags=["query"])
def ask(req: AskRequest, user: User = Depends(current_user)) -> AskResponse:
    result = rag_answer(
        req.question,
        top_k=req.top_k,
        filter_expr=req.filter_expr,
        history=req.history,
        collection=_collection_of(req.kb_id),
    )
    return AskResponse(**result)


@app.get("/files", tags=["ingest"])
def list_files(root: str, user: User = Depends(current_user)) -> dict:
    return {"files": [str(p) for p in iter_files(root)]}


# --------------------------------------------------------------------------- #
# 前端页面
# --------------------------------------------------------------------------- #
if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")
