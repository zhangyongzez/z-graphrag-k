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
    # 模型的推理过程（thinking 模型才有），前端可折叠展示
    reasoning: str = ""
    # 未关联知识库，直接与 LLM 对话
    chat_only: bool = False


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
    """只保留用户有权访问的知识库。

    不自动扩展到「全部可见知识库」——用户必须显式选择。
    未选择时由调用方提示用户先选知识库。
    """
    return [w.model_dump() for w in req.kb_weights if perms.can_read(w.kb_id, user)]


def _do_retrieve(req: ChatRequest, weights: list[dict], collection: str | None):
    """统一检索入口：有知识库权重走加权融合，否则单库检索。"""
    if weights:
        return retrieve_weighted(req.question, weights, top_k=req.top_k)
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


def _save_turn(req: ChatRequest, user: User, answer: str, sources: list[dict]) -> None:
    """把一轮问答写入会话（若指定了 conversation_id）。"""
    if not req.conversation_id:
        return
    _owned_conversation(req.conversation_id, user)
    conv.add_message(req.conversation_id, "user", req.question)
    conv.add_message(req.conversation_id, "assistant", answer, sources)


def _history_of(req: ChatRequest) -> list[tuple[str, str]]:
    """对话历史：优先取数据库（更可靠），否则用前端传的。"""
    if req.conversation_id:
        msgs = conv.list_messages(req.conversation_id)
        return [
            ("human" if m["role"] == "user" else "ai", m["content"])
            for m in msgs
            if m["role"] in ("user", "assistant")
        ]
    return [(m.role, m.content) for m in req.history]


def _msg(key: str) -> str:
    """后端提示信息（按请求语言）。"""
    from kgrag.i18n import is_en

    table = {
        "chat.no_llm_configured": (
            "No LLM configured. Add one in Models and select it in Settings."
            if is_en()
            else "未配置大模型，无法对话。请到「模型管理」添加并在「系统配置」中选择。"
        ),
    }
    return table.get(key, key)


def _llm_ready() -> bool:
    """是否已配置可用的 LLM（模型管理里选中了模型）。"""
    from kgrag.settings import selected_model

    return selected_model("llm") is not None


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest, user: User = Depends(current_user)) -> ChatResponse:
    weights = _filtered_weights(req, user)
    collection = _resolve_collection(req.kb_id, user) if req.kb_id else None
    # 未选知识库 -> 纯对话模式（直接和 LLM 聊，不检索）
    chat_only = not weights and collection is None

    # 未配置 LLM 时：有知识库就降级为纯检索，没有则报错
    if not _llm_ready():
        if chat_only:
            answer = _msg("chat.no_llm_configured")
            _save_turn(req, user, answer, [])
            return ChatResponse(answer=answer, sources=[], chat_only=True)
        docs = _do_retrieve(req, weights, collection)
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
            history=_history_of(req),
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
        conv.add_message(
            req.conversation_id, "assistant", result["answer"],
            result.get("sources"), result.get("reasoning", ""),
        )
    return ChatResponse(
        answer=result["answer"],
        sources=result.get("sources", []),
        reasoning=result.get("reasoning", ""),
        chat_only=result.get("chat_only", False),
    )


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
    collection = _resolve_collection(req.kb_id, user) if req.kb_id else None
    conv_id = req.conversation_id
    if conv_id:
        _owned_conversation(conv_id, user)

    chat_only = not weights and collection is None

    def event_gen() -> Iterator[str]:
        answer = ""
        sources: list[dict] = []
        try:
            # 未选知识库 -> 纯对话，不检索
            if chat_only:
                docs = []
            elif weights:
                docs = retrieve_weighted(req.question, weights, top_k=req.top_k)
            else:
                docs = retrieve(req.question, top_k=req.top_k, collection=collection)
            sources = _source_dicts(docs)
            yield _sse("sources", {"sources": sources})

            # 未配置 LLM：有知识库则降级纯检索，无知识库则报错
            if not _llm_ready():
                if chat_only:
                    yield _sse("error", {"message": _msg("chat.no_llm_configured")})
                    return
                text = _format_retrieval_only(docs)
                yield _sse("retrieval_only", {"enabled": True})
                yield _sse("delta", {"text": text})
                yield _sse("done", {})
                if conv_id:
                    conv.add_message(conv_id, "user", req.question)
                    conv.add_message(conv_id, "assistant", text, sources)
                return

            from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

            from kgrag.llm import get_llm
            from kgrag.retrieval.rag import CHAT_PROMPT, SYSTEM_PROMPT

            if chat_only:
                system_prompt = CHAT_PROMPT
            else:
                system_prompt = SYSTEM_PROMPT.format(context=format_context(docs))

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=req.question),
            ]
            # 历史放在系统提示与当前问题之间
            from kgrag.retrieval.rag import _trim_history

            for role, content in _trim_history(_history_of(req)):
                messages.insert(
                    -1,
                    HumanMessage(content=content) if role == "human"
                    else AIMessage(content=content),
                )
            llm = get_llm(model_id=req.llm_model) if req.llm_model else get_llm()
            from kgrag.retrieval.rag import _extract_reasoning, _extract_text

            reasoning = ""
            for chunk in llm.stream(messages):
                # 部分模型会返回 thinking + text 内容块，分开推送
                think = _extract_reasoning(chunk.content)
                if think:
                    reasoning += think
                    yield _sse("reasoning", {"text": think})
                text = _extract_text(chunk.content)
                if text:
                    answer += text
                    yield _sse("delta", {"text": text})
            yield _sse("done", {})

            if conv_id:
                conv.add_message(conv_id, "user", req.question)
                conv.add_message(conv_id, "assistant", answer, sources, reasoning)
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
