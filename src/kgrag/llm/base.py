"""
LLM 工厂：按 ``LLM_PROVIDER`` 返回 LangChain ``BaseChatModel``。

支持
----
- ``openai``    : 任意 OpenAI 兼容端点（含公司网关、vLLM、DeepSeek 等）
- ``anthropic`` : Claude 官方或兼容网关
- ``ollama``    : 本地 Ollama
- ``mock``      : 不调用模型，返回固定回答（仅测试）
"""

from __future__ import annotations

from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from kgrag.config import Settings
from kgrag.logging import get_logger

logger = get_logger(__name__)


class MockChatModel(BaseChatModel):
    """测试用：不联网，回显固定文本。"""

    reply: str = "（mock 模型）这是占位回答。"

    @property
    def _llm_type(self) -> str:
        return "mock"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._reply_message())])

    def _reply_message(self):
        from langchain_core.messages import AIMessage

        return AIMessage(content=self.reply)


def _build_llm(s: Settings, record: dict | None = None) -> BaseChatModel:
    """按模型记录构造 LLM；``record`` 为空时回退到 .env 配置。"""
    provider = record["provider"] if record else s.llm_provider

    if provider == "mock":
        logger.warning("使用 mock LLM，回答为占位文本")
        return MockChatModel()

    # 连接信息优先取模型记录，其次回退 .env
    api_key = (record or {}).get("api_key") or s.llm_api_key
    base_url = (record or {}).get("base_url") or s.llm_api_base
    model_name = (record or {}).get("model") or s.active_llm_model

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        if not api_key:
            raise ValueError("OpenAI 兼容模型需要 API Key，请在「模型管理」中填写")
        kwargs: dict = {
            "model": model_name or "gpt-4o-mini",
            "api_key": api_key,
            "temperature": s.llm_temperature,
            "max_tokens": s.llm_max_tokens,
            "timeout": s.llm_timeout,
        }
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        if not api_key:
            raise ValueError("Anthropic 模型需要 API Key，请在「模型管理」中填写")
        kwargs = {
            "model": model_name or "claude-sonnet-4-5",
            "api_key": api_key,
            "temperature": s.llm_temperature,
            "max_tokens": s.llm_max_tokens,
            "timeout": s.llm_timeout,
        }
        if base_url:
            kwargs["base_url"] = base_url
        return ChatAnthropic(**kwargs)

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model_name or s.ollama_model,
            base_url=base_url or s.ollama_base_url,
            temperature=s.llm_temperature,
        )

    raise ValueError(f"未知的 LLM 提供方: {provider}")


@lru_cache(maxsize=8)
def get_llm(model_id: str = "") -> BaseChatModel:
    """返回进程内 LLM 单例。

    ``model_id`` 非空时使用指定的模型记录（会话级选择），
    否则使用配置页选中的模型，再否则回退到 .env。
    """
    from kgrag import models as model_store
    from kgrag.settings import effective, effective_settings

    s = effective_settings()
    if not model_id:
        model_id = effective("llm_model_id", "") or ""

    record = model_store.get_raw(model_id) if model_id else None
    if record is not None:
        logger.info(
            f"初始化 LLM: {record['name']}（{record['provider']}/{record['model']}）"
        )
    else:
        logger.info(f"初始化 LLM: provider={s.llm_provider} model={s.active_llm_model}")
    return _build_llm(s, record)


def reset_llm() -> None:
    get_llm.cache_clear()


def available_models() -> list[dict]:
    """可选的模型清单（会话里「选择大模型」用）。

    直接取「模型管理」中登记的 LLM 模型。
    """
    from kgrag import models as model_store

    return [
        {
            "id": m["id"],
            "name": m["name"],
            "label": f"{m['name']}（{m['provider']}）",
            "model": m["model"],
        }
        for m in model_store.list_models("llm")
    ]


def chat(prompt: str) -> str:
    """最小可用封装：单轮问答。"""
    from langchain_core.messages import HumanMessage

    msg: BaseMessage = HumanMessage(content=prompt)
    return get_llm().invoke([msg]).content  # type: ignore[return-value]
