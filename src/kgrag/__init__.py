"""z-graphrag-k：Milvus + LangChain + Neo4j 的可插拔知识库。

.. important::

    **Windows 下的导入顺序**

    本机实测：``faiss``（milvus-lite 的依赖）和 ``transformers`` 如果在
    ``torch`` 之前被导入，会让 torch 的 ``c10.dll`` 初始化失败并抛
    ``OSError: [WinError 1114] 动态链接库(DLL)初始化例程失败``。

    因此只要配置了本地 embedding，就在包被导入的第一时间（早于任何
    langchain / milvus / faiss 的间接导入）预加载 torch。其他模式
    （hash / openai / ollama / mock）不付这个开销。
"""

from __future__ import annotations

import contextlib
import os
import sys

__version__ = "0.1.0"


def _preload_torch() -> None:
    """本地 embedding 模式下提前加载 torch，规避 Windows DLL 初始化顺序问题。"""
    if os.getenv("EMBEDDING_PROVIDER", "local").lower() != "local":
        return
    if "torch" in sys.modules:
        return
    with contextlib.suppress(Exception):  # 未安装或加载失败都不应阻断导入
        import torch  # noqa: F401


_preload_torch()

from kgrag.config import get_settings, settings  # noqa: E402

__all__ = ["__version__", "get_settings", "settings"]
