"""系统运行时配置：管理员在「系统配置」页选择与调整的项，覆盖 .env。

分层优先级：**SQLite 运行时配置 > .env > 代码默认值**

与旧版的关键区别
----------------
**模型本身不在配置页维护。** 配置页只通过下拉框「选择」一个已在
「模型管理」页登记过的模型（``llm_model_id`` / ``embedding_model_id``），
模型的连接信息（地址、密钥、本地路径）跟着模型记录走。

配置页只保留「运行时参数」：设备、批大小、Top-K、切块大小、温度等。

``DEFAULT_CONFIG`` 是一份完整的默认配置，任何时候都能一键恢复。
"""

from __future__ import annotations

from typing import Any

from kgrag.config import get_settings
from kgrag.db import CONFIG_KEYS, load_runtime_config, save_runtime_config
from kgrag.logging import get_logger

logger = get_logger(__name__)

# 改了立即生效的项（下次调用工厂时读取最新值）
HOT_RELOADABLE = {
    "llm_model_id",
    "llm_temperature",
    "llm_max_tokens",
    "llm_timeout",
    "retrieval_top_k",
    "retrieval_score_threshold",
    "chunk_size",
    "chunk_overlap",
    "ad_enabled",
    "ad_server_uri",
    "ad_use_ssl",
    "ad_base_dn",
    "ad_user_filter",
    "ad_bind_dn",
    "ad_bind_password",
    "ad_display_name_attr",
    "ad_email_attr",
}

# 改了必须重启的项（涉及模型重载 / 索引重建）
RESTART_REQUIRED = {
    "embedding_model_id",
    "embedding_device",
    "embedding_batch_size",
    "embedding_max_length",
    "embedding_normalize",
    "vectorstore_provider",
    "milvus_uri",
    "milvus_token",
    "milvus_db_name",
    "vectorstore_lite_path",
}

# 敏感字段：返回给前端时脱敏
SECRET_KEYS = {
    "milvus_token",
    "ad_bind_password",
}

# 默认配置：配置页「恢复默认」时写回
DEFAULT_CONFIG: dict[str, Any] = {
    # 选中的模型（空表示未选择，回退到 .env）
    "llm_model_id": "",
    "embedding_model_id": "",
    # 运行时参数
    "llm_temperature": 0.2,
    "llm_max_tokens": 2048,
    "llm_timeout": 60,
    "embedding_device": "auto",
    "embedding_batch_size": 32,
    "embedding_max_length": 1024,
    "embedding_normalize": True,
    "retrieval_top_k": 5,
    "retrieval_score_threshold": 0.0,
    "chunk_size": 800,
    "chunk_overlap": 120,
    # 向量库
    "vectorstore_provider": "lite",
    "vectorstore_lite_path": "./data/milvus/kgrag.db",
    "milvus_uri": "http://localhost:19530",
    "milvus_token": "",
    "milvus_db_name": "default",
    # AD
    "ad_enabled": False,
    "ad_server_uri": "",
    "ad_use_ssl": False,
    "ad_base_dn": "",
    "ad_user_filter": "(sAMAccountName={username})",
    "ad_bind_dn": "",
    "ad_bind_password": "",
    "ad_display_name_attr": "displayName",
    "ad_email_attr": "mail",
}

# 配置页分组。模型类字段用 ``type=model_select``，前端渲染成下拉。
CONFIG_GROUPS: list[dict[str, Any]] = [
    {
        "key": "embedding",
        "title": "Embedding 模型",
        "restart": True,
        "description": "选择用于把文档和问题转成向量的模型。模型本身在「模型管理」页维护。",
        "fields": [
            {
                "key": "embedding_model_id",
                "label": "Embedding 模型",
                "type": "model_select",
                "model_kind": "embedding",
                "help": "从「模型管理」中已登记的 Embedding 模型里选择。切换模型后需要重建知识库索引。",
            },
            {
                "key": "embedding_device",
                "label": "运行设备",
                "type": "select",
                "options": ["auto", "cpu", "cuda"],
                "help": "本地模型推理设备。auto 会自动检测 GPU，没有则用 CPU；cuda 需已安装 CUDA 版 PyTorch。",
            },
            {
                "key": "embedding_batch_size",
                "label": "批大小",
                "type": "number",
                "help": "一次编码多少条文本。显存不足时调小；CPU 模式下过大会变慢。",
            },
            {
                "key": "embedding_max_length",
                "label": "最大序列长度",
                "type": "number",
                "help": "单条文本最多保留的 token 数，超出部分会被截断。bge-m3 通常为 1024 或 8192。",
            },
            {
                "key": "embedding_normalize",
                "label": "向量归一化",
                "type": "bool",
                "help": "把向量缩放到单位长度。使用余弦相似度时应开启；关闭后需改用内积且注意数值范围。",
            },
        ],
    },
    {
        "key": "llm",
        "title": "大模型",
        "restart": False,
        "description": "选择对话使用的大模型。模型本身在「模型管理」页维护。",
        "fields": [
            {
                "key": "llm_model_id",
                "label": "对话模型",
                "type": "model_select",
                "model_kind": "llm",
                "help": "从「模型管理」中已登记的 LLM 模型里选择。修改后立即生效。",
            },
            {
                "key": "llm_temperature",
                "label": "温度",
                "type": "number",
                "step": "0.1",
                "help": "控制回答的随机性。0 最确定、适合知识问答；0.7~1.0 更有创造性但容易发散。",
            },
            {
                "key": "llm_max_tokens",
                "label": "最大 Tokens",
                "type": "number",
                "help": "单次回答的最大生成长度。过小会截断，过大增加延迟和费用。",
            },
            {
                "key": "llm_timeout",
                "label": "超时（秒）",
                "type": "number",
                "help": "调用大模型的超时时间。网络较慢或模型较慢时调大。",
            },
        ],
    },
    {
        "key": "retrieval",
        "title": "切分与检索",
        "restart": False,
        "description": "文档切分与向量检索的运行时参数。",
        "fields": [
            {
                "key": "retrieval_top_k",
                "label": "默认 Top-K",
                "type": "number",
                "help": "每次检索返回的片段数量。调大召回更全但上下文更长、更慢；一般 3~8。",
            },
            {
                "key": "retrieval_score_threshold",
                "label": "相似度阈值",
                "type": "number",
                "step": "0.01",
                "help": "低于此分数的片段会被丢弃。0 表示不过滤；设为 0.3~0.5 可减少不相关结果。",
            },
            {
                "key": "chunk_size",
                "label": "切块大小",
                "type": "number",
                "help": "每个文本块的最大字符数。太大检索不精准，太小丢失上下文；中文一般 500~1000。",
            },
            {
                "key": "chunk_overlap",
                "label": "切块重叠",
                "type": "number",
                "help": "相邻块之间重叠的字符数，避免语义被切断。一般设为块大小的 10%~20%。",
            },
        ],
    },
    {
        "key": "vectorstore",
        "title": "向量库",
        "restart": True,
        "description": "向量库连接参数。修改后需重启服务。",
        "fields": [
            {
                "key": "vectorstore_provider",
                "label": "提供方",
                "type": "select",
                "options": ["lite", "milvus"],
                "help": (
                    "lite 为嵌入式本地文件，零依赖、无需配置，适合单机；"
                    "milvus 需先部署服务端（docker compose --profile milvus up -d），"
                    "支持多客户端并发。切换后需重启服务。"
                ),
            },
            {
                "key": "vectorstore_lite_path",
                "label": "Lite 路径",
                "type": "text",
                "help": "provider=lite 时生效，向量库文件存放路径。",
            },
            {
                "key": "milvus_uri",
                "label": "Milvus 地址",
                "type": "text",
                "placeholder": "http://localhost:19530",
                "help": (
                    "provider=milvus 时生效。填 Milvus 服务端的 gRPC 地址："
                    "同机部署用 localhost:19530；应用也在 Docker 里要用容器名 "
                    "http://milvus:19530。详见 docs/MILVUS.md"
                ),
            },
            {
                "key": "milvus_token",
                "label": "Milvus Token",
                "type": "password",
                "help": "Milvus 开启了鉴权时填写，否则留空。",
            },
            {
                "key": "milvus_db_name",
                "label": "数据库名",
                "type": "text",
                "help": "Milvus 中的数据库名，默认 default。",
            },
        ],
    },
    {
        "key": "ad",
        "title": "AD 域认证",
        "restart": False,
        "description": "启用后可用域账号登录，本地管理员账号仍然可用。",
        "fields": [
            {
                "key": "ad_enabled",
                "label": "启用 AD 登录",
                "type": "bool",
                "help": "开启后登录页会提示可用域账号登录。",
            },
            {
                "key": "ad_server_uri",
                "label": "LDAP 地址",
                "type": "text",
                "placeholder": "ldap://ad.example.com:389",
                "help": "域控制器地址。明文 LDAP 用 389 端口，LDAPS 用 636 并勾选「使用 SSL」。",
            },
            {
                "key": "ad_use_ssl",
                "label": "使用 SSL",
                "type": "bool",
                "help": "LDAPS 加密连接，生产环境建议开启。",
            },
            {
                "key": "ad_base_dn",
                "label": "Base DN",
                "type": "text",
                "placeholder": "dc=example,dc=com",
                "help": "用户搜索的根节点，填域名的 DC 形式。",
            },
            {
                "key": "ad_user_filter",
                "label": "用户过滤器",
                "type": "text",
                "placeholder": "(sAMAccountName={username})",
                "help": "LDAP 搜索过滤器，{username} 会被替换为登录名。AD 一般用 sAMAccountName。",
            },
            {
                "key": "ad_bind_dn",
                "label": "服务账号 DN",
                "type": "text",
                "help": "用于搜索用户的只读服务账号。留空则匿名绑定（多数 AD 需要填写）。",
            },
            {
                "key": "ad_bind_password",
                "label": "服务账号密码",
                "type": "password",
                "help": "服务账号密码，留空表示不修改。",
            },
            {
                "key": "ad_display_name_attr",
                "label": "显示名属性",
                "type": "text",
                "help": "取哪个 LDAP 属性作为显示名，AD 一般为 displayName。",
            },
            {
                "key": "ad_email_attr",
                "label": "邮箱属性",
                "type": "text",
                "help": "取哪个 LDAP 属性作为邮箱，AD 一般为 mail。",
            },
        ],
    },
]


def effective(key: str, default: Any = None) -> Any:
    """取运行时配置，回退到 .env，再回退到默认值。"""
    rt = load_runtime_config()
    if key in rt:
        return rt[key]
    s = get_settings()
    if hasattr(s, key):
        return getattr(s, key)
    if key in DEFAULT_CONFIG:
        return DEFAULT_CONFIG[key]
    return default


def effective_all() -> dict[str, Any]:
    """合并后的完整配置（用于工厂构造）。

    优先级：**运行时配置 > .env > DEFAULT_CONFIG**。

    ``DEFAULT_CONFIG`` 只作为「配置页有、但 Settings 没有」的键（如
    ``llm_model_id``）的兜底，绝不覆盖 .env 里的值。
    """
    s = get_settings()
    merged = {k: getattr(s, k) for k in s.model_fields}
    # 仅补齐 Settings 里不存在的配置页键
    for k, v in DEFAULT_CONFIG.items():
        merged.setdefault(k, v)
    merged.update(load_runtime_config())
    return merged


def effective_settings():
    """返回应用了运行时配置的 Settings 副本。"""
    from kgrag.config import Settings

    return Settings(**effective_all())


def selected_model(kind: str) -> dict | None:
    """取当前选中的模型记录（带明文 api_key）。未选择时返回 None。"""
    from kgrag import models as model_store

    key = f"{kind}_model_id"
    model_id = effective(key, "")
    if not model_id:
        return None
    return model_store.get_raw(model_id)


def mask(value: Any) -> str:
    """密钥脱敏：保留前 4 后 4。"""
    if not value:
        return ""
    v = str(value)
    if len(v) <= 10:
        return "*" * len(v)
    return f"{v[:4]}{'*' * (len(v) - 8)}{v[-4:]}"


def public_config() -> dict[str, Any]:
    """给配置页用的配置（密钥脱敏）。"""
    rt = load_runtime_config()
    out: dict[str, Any] = {}
    for key in DEFAULT_CONFIG:
        val = rt.get(key, DEFAULT_CONFIG[key])
        if key in SECRET_KEYS:
            out[key] = {"value": mask(val), "is_set": bool(val)}
        else:
            out[key] = val
    return out


def update_config(values: dict[str, Any], updated_by: str = "") -> dict[str, Any]:
    """保存配置，返回 ``{"changed": [...], "restart_required": bool}``。"""
    clean: dict[str, Any] = {}
    for k, v in values.items():
        # 脱敏占位值不落库（前端回传未修改的掩码时忽略）
        if k in SECRET_KEYS and isinstance(v, str) and set(v) <= {"*"}:
            continue
        clean[k] = v

    changed = save_runtime_config(clean, updated_by)
    restart = any(k in RESTART_REQUIRED for k in changed)
    if changed:
        logger.info(f"配置已更新: {changed}（by {updated_by or 'system'}）")

    _invalidate(changed)
    return {"changed": changed, "restart_required": restart}


def reset_config(updated_by: str = "") -> dict[str, Any]:
    """恢复默认配置（清空运行时覆盖）。"""
    from kgrag.db import transaction

    with transaction() as conn:
        conn.execute("DELETE FROM app_config")
    logger.info(f"配置已恢复默认（by {updated_by or 'system'}）")
    _invalidate(list(DEFAULT_CONFIG))
    return {"reset": True, "count": len(DEFAULT_CONFIG)}


def _invalidate(changed: list[str]) -> None:
    if not changed:
        return
    keys = set(changed)
    if keys & {"llm_model_id", "llm_temperature", "llm_max_tokens", "llm_timeout"}:
        from kgrag.llm import reset_llm

        reset_llm()
    if keys & {
        "embedding_model_id",
        "embedding_device",
        "embedding_batch_size",
        "embedding_max_length",
        "embedding_normalize",
    }:
        from kgrag.embeddings import reset_embeddings
        from kgrag.vectorstore import reset_vectorstore

        reset_embeddings()
        reset_vectorstore()
    if keys & {
        "vectorstore_provider",
        "milvus_uri",
        "milvus_token",
        "milvus_db_name",
        "vectorstore_lite_path",
    }:
        from kgrag.vectorstore import reset_vectorstore

        reset_vectorstore()


def config_schema() -> list[dict[str, Any]]:
    """配置页表单结构（附带当前值、默认值、下拉选项）。"""
    from kgrag import models as model_store

    current = public_config()
    groups = []
    for g in CONFIG_GROUPS:
        fields = []
        for f in g["fields"]:
            item = dict(f)
            item["value"] = current.get(f["key"])
            item["default"] = DEFAULT_CONFIG.get(f["key"])
            item["hot"] = f["key"] in HOT_RELOADABLE
            if f.get("type") == "model_select":
                item["options"] = model_store.options_for(f["model_kind"])
            fields.append(item)
        groups.append({**g, "fields": fields})
    return groups


def is_editable(key: str) -> bool:
    return key in CONFIG_KEYS
