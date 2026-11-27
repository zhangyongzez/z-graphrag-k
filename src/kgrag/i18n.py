"""后端国际化：字段标签、帮助文字、错误信息。

语言由请求头 ``Accept-Language`` 决定，前端在切换语言后会带上。
"""

from __future__ import annotations

from contextvars import ContextVar

_lang: ContextVar[str] = ContextVar("lang", default="zh")


def set_lang(lang: str) -> None:
    _lang.set("en" if str(lang).lower().startswith("en") else "zh")


def get_lang() -> str:
    return _lang.get()


def is_en() -> bool:
    return _lang.get() == "en"


def L(zh: str, en: str) -> str:
    """按当前语言二选一。"""
    return en if is_en() else zh


# --------------------------------------------------------------------------- #
# 配置页字段的英文文案。key 与 CONFIG_GROUPS 里的字段 key 一致。
# --------------------------------------------------------------------------- #
FIELD_EN: dict[str, dict[str, str]] = {
    "embedding_model_id": {
        "label": "Embedding Model",
        "help": "Choose a registered Embedding model. Rebuilding the knowledge base index is required after switching.",
    },
    "embedding_device": {
        "label": "Device",
        "help": "Inference device for local models. 'auto' detects GPU; 'cuda' requires CUDA-enabled PyTorch.",
    },
    "embedding_batch_size": {
        "label": "Batch Size",
        "help": "Number of texts encoded at once. Lower it if you run out of VRAM.",
    },
    "embedding_max_length": {
        "label": "Max Sequence Length",
        "help": "Maximum tokens per text; longer text is truncated. bge-m3 is usually 1024 or 8192.",
    },
    "embedding_normalize": {
        "label": "Normalize Vectors",
        "help": "Scale vectors to unit length. Enable it when using cosine similarity.",
    },
    "llm_model_id": {
        "label": "Chat Model",
        "help": "Choose a registered LLM. Takes effect immediately.",
    },
    "llm_temperature": {
        "label": "Temperature",
        "help": "Controls randomness. 0 is most deterministic (good for QA); 0.7-1.0 is more creative.",
    },
    "llm_max_tokens": {
        "label": "Max Tokens",
        "help": "Maximum response length. Too small truncates; too large increases latency and cost.",
    },
    "llm_timeout": {
        "label": "Timeout (seconds)",
        "help": "Request timeout for LLM calls. Increase for slow models or networks.",
    },
    "retrieval_top_k": {
        "label": "Default Top-K",
        "help": "Number of chunks returned per query. Higher recalls more but is slower; typically 3-8.",
    },
    "retrieval_score_threshold": {
        "label": "Similarity Threshold",
        "help": "Chunks below this score are dropped. 0 disables filtering; 0.3-0.5 reduces noise.",
    },
    "chunk_size": {
        "label": "Chunk Size",
        "help": "Max characters per chunk. Too large hurts precision; too small loses context. 500-1000 for Chinese.",
    },
    "chunk_overlap": {
        "label": "Chunk Overlap",
        "help": "Overlapping characters between chunks. Usually 10%-20% of chunk size.",
    },
    "vectorstore_provider": {
        "label": "Provider",
        "help": "lite is an embedded local file with zero setup, good for single-node. milvus connects to a standalone server (docker compose --profile milvus up -d) supporting concurrent clients. Restart required.",
    },
    "vectorstore_lite_path": {
        "label": "Lite Path",
        "help": "Active when provider=lite. Path to the vector store file.",
    },
    "milvus_uri": {
        "label": "Milvus URI",
        "help": "Active when provider=milvus. gRPC address of the server: localhost:19530 for same host, http://milvus:19530 when the app also runs in Docker. See docs/MILVUS.md",
    },
    "milvus_token": {
        "label": "Milvus Token",
        "help": "Required only if the Milvus server has authentication enabled.",
    },
    "milvus_db_name": {
        "label": "Database Name",
        "help": "Milvus database name. Defaults to 'default'.",
    },
    "ad_enabled": {
        "label": "Enable AD Login",
        "help": "Shows a domain login hint on the sign-in page.",
    },
    "ad_server_uri": {
        "label": "LDAP URI",
        "help": "Domain controller address. Port 389 for LDAP, 636 for LDAPS (enable SSL).",
    },
    "ad_use_ssl": {
        "label": "Use SSL",
        "help": "Encrypted LDAPS connection. Recommended in production.",
    },
    "ad_base_dn": {
        "label": "Base DN",
        "help": "Root for user searches, e.g. dc=example,dc=com",
    },
    "ad_user_filter": {
        "label": "User Filter",
        "help": "LDAP search filter; {username} is replaced with the login name. AD usually uses sAMAccountName.",
    },
    "ad_bind_dn": {
        "label": "Bind DN",
        "help": "Read-only service account used to search users. Leave blank for anonymous bind.",
    },
    "ad_bind_password": {
        "label": "Bind Password",
        "help": "Service account password. Leave blank to keep unchanged.",
    },
    "ad_display_name_attr": {
        "label": "Display Name Attribute",
        "help": "LDAP attribute used as the display name. AD usually uses displayName.",
    },
    "ad_email_attr": {
        "label": "Email Attribute",
        "help": "LDAP attribute used as the email. AD usually uses mail.",
    },
}

GROUP_EN: dict[str, dict[str, str]] = {
    "embedding": {
        "title": "Embedding Model",
        "description": "Choose the model that turns documents and questions into vectors. Models are managed in the Models page.",
    },
    "llm": {
        "title": "LLM",
        "description": "Choose the chat model. Models are managed in the Models page.",
    },
    "retrieval": {
        "title": "Chunking & Retrieval",
        "description": "Runtime parameters for document chunking and vector retrieval.",
    },
    "vectorstore": {
        "title": "Vector Store",
        "description": "Vector store connection settings. Restart required after changes.",
    },
    "ad": {
        "title": "AD Authentication",
        "description": "Once enabled, users can sign in with domain accounts. The local admin account still works.",
    },
}


def translate_field(field: dict) -> dict:
    """把字段的 label/help 换成当前语言。"""
    if not is_en():
        return field
    en = FIELD_EN.get(field.get("key", ""))
    if not en:
        return field
    out = dict(field)
    out["label"] = en.get("label", field.get("label"))
    out["help"] = en.get("help", field.get("help"))
    return out


def translate_group(group: dict) -> dict:
    if not is_en():
        return group
    en = GROUP_EN.get(group.get("key", ""))
    if not en:
        return group
    return {
        **group,
        "title": en.get("title", group.get("title")),
        "description": en.get("description", group.get("description")),
    }
