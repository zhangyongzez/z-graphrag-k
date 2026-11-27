"""RAG 问答链：检索 → 组装上下文 → 调用 LLM。"""

from __future__ import annotations

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from kgrag.llm import get_llm
from kgrag.logging import get_logger
from kgrag.settings import effective_settings
from kgrag.vectorstore import get_retriever

logger = get_logger(__name__)

SYSTEM_PROMPT = """你是一个严谨的知识库助手。请仅根据下面提供的资料回答问题。

要求：
1. 如果资料中没有相关信息，直接说「根据现有资料无法回答」，不要编造。
2. 回答要简洁准确，必要时引用来源文件名。
3. 用中文回答，除非用户使用其他语言提问。

资料：
{context}
"""


def format_context(docs: list[Document]) -> str:
    if not docs:
        return "（无相关资料）"
    parts = []
    for i, d in enumerate(docs, 1):
        src = d.metadata.get("filename") or d.metadata.get("source", "unknown")
        parts.append(f"[{i}] 来源：{src}\n{d.page_content}")
    return "\n\n".join(parts)


def retrieve(
    question: str,
    *,
    top_k: int | None = None,
    filter_expr: str | None = None,
    collection: str | None = None,
) -> list[Document]:
    retriever = get_retriever(top_k=top_k, filter_expr=filter_expr, collection=collection)
    return retriever.invoke(question)


def retrieve_weighted(
    question: str,
    kb_weights: list[dict],
    *,
    top_k: int | None = None,
    filter_expr: str | None = None,
) -> list[Document]:
    """跨多个知识库检索，按权重融合排序。

    ``kb_weights`` 形如 ``[{"kb_id": "abc", "weight": 1.0}, ...]``。
    每个库按 ``top_k`` 取候选，再按 ``score * weight`` 重排，取前 ``top_k``。

    注意：不同 collection 的相似度分数尺度可能不同，这里用 min-max 归一化
    后再乘权重，避免某库分数普遍偏高导致权重失效。
    """
    from kgrag.kb import get_registry

    if not kb_weights:
        return retrieve(question, top_k=top_k, filter_expr=filter_expr)

    s = effective_settings()
    k = top_k or s.retrieval_top_k
    reg = get_registry()

    # 单库时退化为普通检索
    if len(kb_weights) == 1:
        kb = reg.get(kb_weights[0]["kb_id"])
        if kb is None:
            return []
        return retrieve(
            question, top_k=k, filter_expr=filter_expr, collection=kb.collection
        )

    scored: list[tuple[float, Document]] = []
    for item in kb_weights:
        kb = reg.get(item.get("kb_id", ""))
        if kb is None:
            continue
        weight = float(item.get("weight", 1.0))
        try:
            docs = retrieve(
                question, top_k=k, filter_expr=filter_expr, collection=kb.collection
            )
        except Exception as e:  # noqa: BLE001 - 单个库失败不影响整体
            logger.warning(f"知识库 {kb.name} 检索失败: {e}")
            continue
        if not docs:
            continue
        scores = [d.metadata.get("score") or 0.0 for d in docs]
        lo, hi = min(scores), max(scores)
        span = (hi - lo) or 1.0
        for d, raw in zip(docs, scores, strict=True):
            norm = (raw - lo) / span if hi > lo else 1.0
            d.metadata["kb_id"] = kb.id
            d.metadata["kb_name"] = kb.name
            d.metadata["weighted_score"] = norm * weight
            scored.append((norm * weight, d))

    scored.sort(key=lambda x: x[0], reverse=True)
    merged = [d for _, d in scored[:k]]
    return merged


def answer(
    question: str,
    *,
    top_k: int | None = None,
    filter_expr: str | None = None,
    history: list[tuple[str, str]] | None = None,
    collection: str | None = None,
    kb_weights: list[dict] | None = None,
    llm_model: str = "",
) -> dict:
    """检索并回答。

    Returns
    -------
    dict: ``{"answer": str, "sources": list[dict]}``
    """
    s = effective_settings()
    if kb_weights:
        docs = retrieve_weighted(
            question, kb_weights, top_k=top_k, filter_expr=filter_expr
        )
    else:
        docs = retrieve(question, top_k=top_k, filter_expr=filter_expr, collection=collection)

    messages: list[BaseMessage] = [
        SystemMessage(content=SYSTEM_PROMPT.format(context=format_context(docs)))
    ]
    for role, content in history or []:
        messages.append(HumanMessage(content=content) if role == "human" else AIMessage(content=content))
    messages.append(HumanMessage(content=question))

    logger.debug(f"检索到 {len(docs)} 条资料，调用 LLM provider={s.llm_provider}")
    llm = get_llm(model_id=llm_model) if llm_model else get_llm()
    resp = llm.invoke(messages)

    sources = [
        {
            "filename": d.metadata.get("filename"),
            "source": d.metadata.get("source"),
            "chunk_index": d.metadata.get("chunk_index"),
            "score": d.metadata.get("score"),
            "weighted_score": d.metadata.get("weighted_score"),
            "kb_id": d.metadata.get("kb_id"),
            "kb_name": d.metadata.get("kb_name"),
            "preview": d.page_content[:120],
        }
        for d in docs
    ]
    return {"answer": _extract_text(resp.content), "sources": sources,
            "reasoning": _extract_reasoning(resp.content)}


def _extract_text(content) -> str:
    """从 LangChain 返回的 content 中取出正文。

    多数模型返回 str，但部分模型（如带 thinking 的 DeepSeek / Claude）
    会返回内容块列表，形如::

        [{"type": "thinking", "thinking": "..."},
         {"type": "text", "text": "真正的回答"}]

    这里只取 text 块；推理过程由 :func:`_extract_reasoning` 单独取出。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(block["text"])
            elif hasattr(block, "text"):
                parts.append(str(block.text))
        return "\n".join(parts).strip()
    return str(content)


def _extract_reasoning(content) -> str:
    """取出模型的推理过程（thinking 块），没有则返回空串。"""
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "thinking" and block.get("thinking"):
                parts.append(str(block["thinking"]))
        else:
            # pydantic 对象形式
            btype = getattr(block, "type", None)
            if btype == "thinking":
                text = getattr(block, "thinking", "") or getattr(block, "text", "")
                if text:
                    parts.append(str(text))
    return "\n".join(parts).strip()
