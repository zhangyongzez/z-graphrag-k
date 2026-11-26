"""
Embedding 工厂：按 ``EMBEDDING_PROVIDER`` 返回 LangChain ``Embeddings`` 实例。

支持的 provider
--------------
- ``local``  : sentence-transformers 本地推理（bge-m3 等）
- ``openai`` : OpenAI 兼容的 /v1/embeddings 接口
- ``ollama`` : Ollama 服务
- ``hash``   : 确定性哈希向量，无语义，仅用于离线跑通链路 / 单测
"""

from __future__ import annotations

import hashlib
import math
from functools import lru_cache

from langchain_core.embeddings import Embeddings

from kgrag.config import Settings
from kgrag.logging import get_logger

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# 自定义实现
# --------------------------------------------------------------------------- #
class HashEmbeddings(Embeddings):
    """确定性哈希向量。

    仅用于在没有模型、没有网络的环境下验证整条链路。它把 token 哈希到
    固定维度上，能保证同一文本得到同一向量、相似文本有一定重合，
    但**没有真正的语义能力**，生产环境必须替换。
    """

    def __init__(self, dim: int = 1024, normalize: bool = True) -> None:
        self.dim = dim
        self.normalize = normalize

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        # 按字符 bigram 打散，避免中文单字哈希碰撞过密
        tokens = [text[i : i + 2] for i in range(max(len(text) - 1, 1))]
        if not tokens:
            tokens = [text]
        for tok in tokens:
            h = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "big") % self.dim
            sign = 1.0 if h[4] & 1 else -1.0
            vec[idx] += sign
        if self.normalize:
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vec = [v / norm for v in vec]
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class LocalEmbeddings(Embeddings):
    """sentence-transformers 本地推理，带 GPU 自动降级。

    - 显存不足时自动把 batch 减半重试
    - 支持 bge 系列（自动加 query instruction）
    """

    def __init__(
        self,
        model_path: str,
        device: str = "auto",
        batch_size: int = 32,
        max_length: int = 1024,
        normalize: bool = True,
    ) -> None:
        # Windows 上必须先 import torch 再 import sentence_transformers，
        # 否则 transformers 会先加载 numpy/DLL，导致 torch 的 c10.dll 初始化失败
        # (WinError 1114)。见 https://github.com/pytorch/pytorch/issues/ 相关讨论。
        import torch  # noqa: F401
        from sentence_transformers import SentenceTransformer

        self.batch_size = batch_size
        self.normalize = normalize
        self._device = self._resolve_device(device)
        logger.info(f"加载 embedding 模型: {model_path} (device={self._device})")
        self._model = SentenceTransformer(model_path, device=self._device)
        self._model.max_seq_length = max_length
        dim = getattr(
            self._model, "get_embedding_dimension", None
        ) or self._model.get_sentence_embedding_dimension
        logger.info(f"模型就绪: dim={dim()}, max_seq_length={max_length}")

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device != "auto":
            return device
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    def _encode(self, texts: list[str], batch_size: int) -> list[list[float]]:
        vecs = self._model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=self.normalize,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return vecs.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        bs = self.batch_size
        while True:
            try:
                return self._encode(texts, bs)
            except Exception as e:  # noqa: BLE001 - 显存不足时降级
                if bs <= 1 or "out of memory" not in str(e).lower():
                    raise
                bs //= 2
                logger.warning(f"显存不足，batch_size 降为 {bs} 重试")
                try:
                    import torch

                    torch.cuda.empty_cache()
                except ImportError:
                    pass

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


# --------------------------------------------------------------------------- #
# 工厂
# --------------------------------------------------------------------------- #
def _build_embeddings(s: Settings, record: dict | None = None) -> Embeddings:
    """按模型记录构造 Embedding；``record`` 为空时回退到 .env 配置。"""
    provider = record["provider"] if record else s.embedding_provider
    dim = (record or {}).get("dim") or s.embedding_dim
    api_key = (record or {}).get("api_key") or s.embedding_api_key
    base_url = (record or {}).get("base_url") or s.embedding_api_base
    model_name = (record or {}).get("model") or s.embedding_model

    if provider == "hash":
        logger.warning("使用 hash embedding 占位实现，向量无语义，仅用于验证链路")
        return HashEmbeddings(dim=dim, normalize=s.embedding_normalize)

    if provider == "local":
        # 本地模型路径优先取模型记录
        model_path = (record or {}).get("path") or s.embedding_source
        return LocalEmbeddings(
            model_path=model_path,
            device=s.embedding_device,
            batch_size=s.embedding_batch_size,
            max_length=s.embedding_max_length,
            normalize=s.embedding_normalize,
        )

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        if not api_key:
            raise ValueError("OpenAI 兼容 Embedding 需要 API Key，请在「模型管理」中填写")
        kwargs: dict = {
            "model": model_name,
            "api_key": api_key,
        }
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAIEmbeddings(**kwargs)

    if provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(
            model=model_name, base_url=base_url or s.ollama_base_url
        )

    raise ValueError(f"未知的 Embedding 提供方: {provider}")


@lru_cache(maxsize=1)
def get_embeddings() -> Embeddings:
    """返回进程内单例 Embedding 实例（模型加载开销大）。

    优先使用配置页选中的模型记录，回退到 .env。
    """
    from kgrag import models as model_store
    from kgrag.settings import effective, effective_settings

    s = effective_settings()
    model_id = effective("embedding_model_id", "") or ""
    record = model_store.get_raw(model_id) if model_id else None
    if record is not None:
        logger.info(f"初始化 Embedding: {record['name']}（{record['provider']}）")
    else:
        logger.info(f"初始化 Embedding: provider={s.embedding_provider} model={s.embedding_model}")
    return _build_embeddings(s, record)


def reset_embeddings() -> None:
    """清空缓存（测试或热切换配置时使用）。"""
    get_embeddings.cache_clear()
