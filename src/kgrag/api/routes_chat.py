"""对话接口：会话管理 + 普通问答 + 流式输出。"""

from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from kgrag import conversations as conv
from kgrag.api.deps import current_user
from kgrag.auth.service import User
from kgrag.kb import get_registry
from kgrag.kb import permissions as perms
from kgrag.logging import get_logger
from kgrag.retrieval import answer as rag_answer
from kgrag.retrieval.rag import format_context, retrieve, retrieve_weighted

logger = get_logger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str


class KBWeight(BaseModel):
    kb_id: str
    weight: float = 1.0


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    kb_id: str | None = None
    kb_weights: list[KBWeight] = Field(default_factory=list)
    top_k: int | None = None
    history: list[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    conversation_id: str | None = None
    llm_model: str = ""


class SourceOut(BaseModel):
    filename: str | None = None
    source: str | None = None
    chunk_index: int | None = None
    score: float | None = None
    weighted_score: float | None = None
    kb_id: str | None = None
    kb_name: str | None = None
    preview: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceOut]
    # 未配置 LLM 时降级为纯检索，answer 是原文片段而非模型生成
    retrieval_only: bool = False


# --------------------------------------------------------------------------- #
# 会话 CRUD
# --------------------------------------------------------------------------- #
class ConvCreate(BaseModel):
    title: str = "新对话"
    kb_weights: list[KBWeight] = Field(default_factory=list)
    llm_model: str = ""


class ConvUpdate(BaseModel):
    title: str | None = None
    kb_weights: list[KBWeight] | None = None
    llm_model: str | None = None


def _owned_conversation(conv_id: str, user: User) -> dict:
    c = conv.get_conversation(conv_id)
    if c is None:
        raise HTTPException(404, "会话不存在")
    if c["user_id"] != user.id and not user.is_admin:
        raise HTTPException(403, "无权访问该会话")
    return c


@router.get("/conversations")
def list_conversations(user: User = Depends(current_user)) -> list[dict]:
    return conv.list_conversations(user.id)


@router.post("/conversations", status_code=201)
def create_conversation(req: ConvCreate, user: User = Depends(current_user)) -> dict:
    return conv.create_conversation(
        user.id,
        title=req.title,
        kb_weights=[w.model_dump() for w in req.kb_weights],
        llm_model=req.llm_model,
    )


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: str, user: User = Depends(current_user)) -> dict:
    c = _owned_conversation(conv_id, user)
    c["messages"] = conv.list_messages(conv_id)
    return c


@router.patch("/conversations/{conv_id}")
def update_conversation(
    conv_id: str,
    req: ConvUpdate,
    user: User = Depends(current_user),
) -> dict:
    _owned_conversation(conv_id, user)
    conv.update_conversation(
        conv_id,
        title=req.title,
        kb_weights=[w.model_dump() for w in req.kb_weights] if req.kb_weights is not None else None,
        llm_model=req.llm_model,
    )
    return conv.get_conversation(conv_id)  # type: ignore[return-value]


@router.delete("/conversations/{conv_id}", status_code=204)
def delete_conversation(conv_id: str, user: User = Depends(current_user)) -> None:
    _owned_conversation(conv_id, user)
    conv.delete_conversation(conv_id)


# --------------------------------------------------------------------------- #
# 检索
# --------------------------------------------------------------------------- #
def _resolve_collection(kb_id: str | None, user: User) -> str | None:
    if not kb_id:
        return None
    kb = get_registry().get(kb_id)
    if kb is None:
        raise HTTPException(404, f"知识库不存在: {kb_id}")
    if not perms.can_read(kb_id, user):
        raise HTTPException(403, "无权访问该知识库")
    return kb.collection


def _filtered_weights(req: ChatRequest, user: User) -> list[dict]:
    """只保留用户有权访问的知识库。"""
    out = []
    for w in req.kb_weights:
        if perms.can_read(w.kb_id, user):
            out.append(w.model_dump())
    return out


def _do_retrieve(req: ChatRequest, user: User):
    weights = _filtered_weights(req, user)
    if weights:
        return retrieve_weighted(req.question, weights, top_k=req.top_k)
    collection = _resolve_collection(req.kb_id, user)
    return retrieve(req.question, top_k=req.top_k, collection=collection)


def _source_dicts(docs) -> list[dict]:
    return [
        {
            "filename": d.metadata.get("filename"),
            "source": d.metadata.get("source"),
            "chunk_index": d.metadata.get("chunk_index"),
            "score": d.metadata.get("score"),
            "weighted_score": d.metadata.get("weighted_score"),
            "kb_id": d.metadata.get("kb_id"),
            "kb_name": d.metadata.get("kb_name"),
            "preview": d.page_content[:200],
        }
        for d in docs
    ]


def _build_sources(docs) -> list[dict]:
    return [
        {
            "filename": d.metadata.get("filename"),
            "source": d.metadata.get("source"),
            "chunk_index": d.metadata.get("chunk_index"),
            "score": d.metadata.get("score"),
            "weighted_score": d.metadata.get("weighted_score"),
            "kb_id": d.metadata.get("kb_id"),
            "kb_name": d.metadata.get("kb_name"),
            "preview": d.page_content[:200],
        }
        for d in docs
    ]


def _llm_ready() -> bool:
    """是否已配置可用的 LLM（模型管理里选中了模型）。"""
    from kgrag.settings import selected_model

    return selected_model("llm") is not None


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest, user: User = Depends(current_user)) -> ChatResponse:
    weights = _filtered_weights(req, user)
    collection = None if weights else _resolve_collection(req.kb_id, user)

    # 未配置 LLM 时降级为「纯检索」：返回命中的原文片段，不做生成
    if not _llm_ready():
        docs = (
            retrieve_weighted(req.question, weights, top_k=req.top_k)
            if weights
            else retrieve(req.question, top_k=req.top_k, collection=collection)
        )
        sources = _build_sources(docs)
        answer = _format_retrieval_only(docs)
        if req.conversation_id:
            _owned_conversation(req.conversation_id, user)
            conv.add_message(req.conversation_id, "user", req.question)
            conv.add_message(req.conversation_id, "assistant", answer, sources)
        return ChatResponse(answer=answer, sources=sources, retrieval_only=True)

    try:
        result = rag_answer(
            req.question,
            top_k=req.top_k,
            history=[(m.role, m.content) for m in req.history],
            collection=collection,
            kb_weights=weights or None,
            llm_model=req.llm_model,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("问答失败")
        raise HTTPException(500, f"问答失败: {e}") from e

    if req.conversation_id:
        _owned_conversation(req.conversation_id, user)
        conv.add_message(req.conversation_id, "user", req.question)
        conv.add_message(req.conversation_id, "assistant", result["answer"],
                         result.get("sources"))
    return ChatResponse(**result)


def _format_retrieval_only(docs) -> str:
    """未配置 LLM 时的回答：直接返回命中的原文片段。"""
    if not docs:
        return "没有检索到相关内容。\n\n（当前未配置大模型，仅展示检索结果。"
    lines = [
        f"未配置大模型，以下是检索到的 {len(docs)} 条相关片段：\n"
    ]
    for i, d in enumerate(docs, 1):
        src = d.metadata.get("filename") or "未知来源"
        lines.append(f"【{i}】{src}\n{d.page_content.strip()}\n")
    lines.append("提示：在「模型管理」中添加大模型并在「系统配置」中选择后，可获得总结性回答。")
    return "\n".join(lines)


@router.post("/stream")
def chat_stream(req: ChatRequest, user: User = Depends(current_user)) -> StreamingResponse:
    """SSE 流式输出。

    事件格式：
    - ``event: sources`` 附带命中的资料
    - ``event: delta``   增量文本
    - ``event: done``    结束
    """
    weights = _filtered_weights(req, user)
    collection = None if weights else _resolve_collection(req.kb_id, user)
    conv_id = req.conversation_id
    if conv_id:
        _owned_conversation(conv_id, user)

    def event_gen() -> Iterator[str]:
        answer = ""
        sources: list[dict] = []
        try:
            if weights:
                docs = retrieve_weighted(req.question, weights, top_k=req.top_k)
            else:
                docs = retrieve(req.question, top_k=req.top_k, collection=collection)
            sources = _source_dicts(docs)
            yield _sse("sources", {"sources": sources})

            # 未配置 LLM：降级为纯检索，直接把片段流式吐出来
            if not _llm_ready():
                text = _format_retrieval_only(docs)
                yield _sse("retrieval_only", {"enabled": True})
                yield _sse("delta", {"text": text})
                yield _sse("done", {})
                if conv_id:
                    conv.add_message(conv_id, "user", req.question)
                    conv.add_message(conv_id, "assistant", text, sources)
                return

            from langchain_core.messages import HumanMessage, SystemMessage

            from kgrag.llm import get_llm
            from kgrag.retrieval.rag import SYSTEM_PROMPT

            messages = [
                SystemMessage(content=SYSTEM_PROMPT.format(context=format_context(docs))),
                HumanMessage(content=req.question),
            ]
            for m in req.history:
                cls = HumanMessage if m.role == "human" else None
                if cls:
                    messages.insert(-1, cls(content=m.content))
            llm = get_llm(model_id=req.llm_model) if req.llm_model else get_llm()
            for chunk in llm.stream(messages):
                text = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                if text:
                    answer += text
                    yield _sse("delta", {"text": text})
            yield _sse("done", {})

            if conv_id:
                conv.add_message(conv_id, "user", req.question)
                conv.add_message(conv_id, "assistant", answer, sources)
        except Exception as e:  # noqa: BLE001
            logger.exception("流式问答失败")
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
