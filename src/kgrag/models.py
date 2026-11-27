"""模型注册表：统一管理 LLM 与 Embedding 模型。

设计原则
--------
**模型在这里维护，配置页只做选择。**

每个模型记录自带完整连接信息（provider / 模型标识 / API 地址 / 密钥 /
本地路径），所以「新增一个模型」= 新增一条记录，配置页立刻多一个下拉选项。

支持的模型类型
--------------
- ``llm``       : 对话模型（anthropic / openai 兼容 / ollama / mock）
- ``embedding`` : 向量模型（local 本地目录 / openai 兼容 / ollama / hash）

本地 Embedding 模型的目录结构（sentence-transformers 约定）::

    models/bge-m3/
    ├── config.json
    ├── model.safetensors 或 pytorch_model.bin
    ├── tokenizer.json
    └── ...
"""

from __future__ import annotations

import contextlib
import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from kgrag.config import get_settings
from kgrag.db import execute, query, query_one
from kgrag.logging import get_logger

logger = get_logger(__name__)

KIND_LLM = "llm"
KIND_EMBEDDING = "embedding"
KINDS = (KIND_LLM, KIND_EMBEDDING)

# 每类模型允许的 provider
PROVIDERS = {
    KIND_LLM: ("anthropic", "openai", "ollama", "mock"),
    KIND_EMBEDDING: ("local", "openai", "ollama", "hash"),
}

# 判定一个目录是不是 sentence-transformers 模型目录
_MODEL_MARKERS = ("config.json",)
_WEIGHT_MARKERS = (
    "model.safetensors",
    "pytorch_model.bin",
    "model.onnx",
    "openvino_model.xml",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# 目录
# --------------------------------------------------------------------------- #
def models_root() -> Path:
    """模型根目录：``<data_dir>/../models``，默认项目下 ``models/``。"""
    s = get_settings()
    root = Path(s.data_dir).parent / "models"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            with contextlib.suppress(OSError):
                total += p.stat().st_size
    return total


def inspect_local(path: str | Path) -> dict:
    """检查目录是否是可用本地模型，返回维度等元信息。"""
    p = Path(path)
    if not p.is_dir():
        return {"ok": False, "message": f"目录不存在: {p}"}
    missing = [m for m in _MODEL_MARKERS if not (p / m).exists()]
    if missing:
        return {"ok": False, "message": f"缺少 {', '.join(missing)}，不是模型目录"}
    has_weight = any((p / w).exists() for w in _WEIGHT_MARKERS)
    if not has_weight:
        return {
            "ok": False,
            "message": "未找到权重文件（model.safetensors / pytorch_model.bin）",
        }

    dim = None
    try:
        cfg = json.loads((p / "config.json").read_text(encoding="utf-8"))
        dim = cfg.get("hidden_size") or cfg.get("dim") or cfg.get("word_embed_proj_dim")
    except (json.JSONDecodeError, OSError):
        pass
    return {"ok": True, "message": "模型目录有效", "dim": dim}


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def _row_to_dict(row) -> dict:
    d = dict(row)
    d["has_api_key"] = bool(d.get("api_key"))
    # 密钥永不返回明文
    d.pop("api_key", None)
    return d


def list_models(kind: str | None = None) -> list[dict]:
    if kind:
        rows = query(
            "SELECT * FROM models WHERE kind = ? ORDER BY created_at DESC", (kind,)
        )
    else:
        rows = query("SELECT * FROM models ORDER BY kind, created_at DESC")
    return [_row_to_dict(r) for r in rows]


def get_model(model_id: str) -> dict | None:
    row = query_one("SELECT * FROM models WHERE id = ?", (model_id,))
    return _row_to_dict(row) if row else None


def get_raw(model_id: str) -> dict | None:
    """内部用：带明文 api_key，供工厂构造客户端。"""
    row = query_one("SELECT * FROM models WHERE id = ?", (model_id,))
    return dict(row) if row else None


def get_by_name(name: str) -> dict | None:
    row = query_one("SELECT * FROM models WHERE name = ?", (name,))
    return dict(row) if row else None


def options_for(kind: str) -> list[dict]:
    """给配置页下拉用的精简清单。"""
    return [
        {"id": m["id"], "name": m["name"], "provider": m["provider"], "model": m["model"]}
        for m in list_models(kind)
    ]


# --------------------------------------------------------------------------- #
# 变更
# --------------------------------------------------------------------------- #
def _validate(kind: str, provider: str, **fields) -> None:
    if kind not in KINDS:
        raise ValueError(f"未知模型类型: {kind}")
    if provider not in PROVIDERS[kind]:
        raise ValueError(
            f"{kind} 不支持的提供方: {provider}（可选 {', '.join(PROVIDERS[kind])}）"
        )


# 模型名前缀 -> 实际需要的 provider。用于提前发现「用 Anthropic 协议调 DeepSeek」这类错配。
_MODEL_PROVIDER_HINTS = {
    "deepseek": "openai",
    "qwen": "openai",
    "glm": "openai",
    "moonshot": "openai",
    "gpt-": "openai",
    "o1-": "openai",
    "claude-": "anthropic",
}


def check_provider_mismatch(provider: str, model: str) -> str | None:
    """检查模型名与提供方是否明显不匹配，返回警告文本或 None。

    只在「明显错误」时提醒，不阻止保存——有些网关会用自定义命名。
    """
    if not model:
        return None
    lower = model.lower()
    for prefix, expected in _MODEL_PROVIDER_HINTS.items():
        if lower.startswith(prefix) and provider != expected:
            return (
                f"模型名「{model}」通常对应 {expected} 协议，"
                f"但你选择的是 {provider}。如果这是公司网关的自定义模型，可忽略此提示。"
            )
    return None


def check_api_key(api_key: str, base_url: str) -> str | None:
    """检查 API Key 填得对不对，返回错误文本或 None。

    最常见的错误是把地址填进 Key 字段，导致网关返回
    ``Invalid token``，很难自己发现。
    """
    key = (api_key or "").strip()
    if not key:
        return None  # 空值由各 provider 自己判断

    low = key.lower()
    if low.startswith(("http://", "https://")):
        return (
            f"API Key 填的是网址（{key[:40]}），不是密钥。"
            "请填写服务商提供的 token，例如 sk-xxxx。"
        )
    if base_url and key.rstrip("/") == base_url.rstrip("/"):
        return "API Key 与 API 地址相同，请填写真正的密钥。"
    if " " in key:
        return "API Key 中不能包含空格，请检查是否复制多行。"
    return None


def create_model(
    *,
    name: str,
    kind: str,
    provider: str,
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    path: str = "",
    dim: int | None = None,
    note: str = "",
) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("模型名称不能为空")
    _validate(kind, provider)

    # API Key 填错（比如填成地址）是硬错误，直接拒绝
    key_err = check_api_key(api_key, base_url)
    if key_err:
        raise ValueError(key_err)

    if query_one("SELECT id FROM models WHERE name = ?", (name,)):
        raise ValueError(f"模型名称已存在: {name}")

    # 本地模型：校验目录并自动取维度
    if provider == "local":
        if not path:
            raise ValueError("本地模型必须填写模型目录路径")
        info = inspect_local(path)
        if not info["ok"]:
            raise ValueError(info["message"])
        dim = dim or info.get("dim")
        path = str(Path(path).expanduser().resolve())

    model_id = uuid.uuid4().hex[:12]
    now = _now()
    execute(
        """INSERT INTO models(id, name, kind, provider, model, base_url, api_key,
                              path, dim, note, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            model_id,
            name,
            kind,
            provider,
            model,
            base_url,
            api_key,
            path,
            dim,
            note,
            now,
            now,
        ),
    )
    logger.info(f"新增模型: {name} ({kind}/{provider})")
    return get_model(model_id)  # type: ignore[return-value]


def update_model(model_id: str, **fields) -> dict:
    row = get_raw(model_id)
    if row is None:
        raise KeyError(f"模型不存在: {model_id}")

    allowed = {
        "name",
        "provider",
        "model",
        "base_url",
        "api_key",
        "path",
        "dim",
        "note",
    }
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        # 空字符串的 api_key 表示「不修改」，避免前端回传空覆盖已有密钥
        if k == "api_key" and v == "":
            continue
        sets.append(f"{k} = ?")
        params.append(v)
    if not sets:
        return get_model(model_id)  # type: ignore[return-value]

    sets.append("updated_at = ?")
    params.append(_now())
    params.append(model_id)
    execute(f"UPDATE models SET {', '.join(sets)} WHERE id = ?", tuple(params))
    return get_model(model_id)  # type: ignore[return-value]


def delete_model(model_id: str, *, remove_files: bool = False) -> None:
    row = get_raw(model_id)
    if row is None:
        raise KeyError(f"模型不存在: {model_id}")
    execute("DELETE FROM models WHERE id = ?", (model_id,))
    if remove_files and row.get("path"):
        p = Path(row["path"])
        if p.is_dir() and models_root().resolve() in p.resolve().parents:
            shutil.rmtree(p, ignore_errors=True)
            logger.info(f"已删除模型目录: {p}")


# --------------------------------------------------------------------------- #
# 上传 / 导入
# --------------------------------------------------------------------------- #
def save_upload(filename: str, data: bytes) -> dict:
    """接收上传的单个文件，落到 ``models/`` 下。"""
    root = models_root()
    target = (root / filename).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise ValueError("非法路径")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"path": str(target), "size": len(data)}


def import_directory(src: str, name: str = "") -> str:
    """把外部目录整个复制到 ``models/`` 下，返回目标路径。"""
    s = Path(src).expanduser()
    info = inspect_local(s)
    if not info["ok"]:
        raise ValueError(info["message"])
    dest = models_root() / (name or s.name)
    if dest.exists():
        raise ValueError(f"目标目录已存在: {dest}")
    shutil.copytree(s, dest)
    return str(dest)


# --------------------------------------------------------------------------- #
# 迁移：旧 local_models 表 -> 统一 models 表
# --------------------------------------------------------------------------- #
def migrate_legacy() -> int:
    """把旧版 ``local_models`` 里的记录迁移到 ``models``（kind=embedding）。"""
    exists = query_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='local_models'"
    )
    if exists is None:
        return 0
    rows = query("SELECT * FROM local_models")
    migrated = 0
    for r in rows:
        if query_one("SELECT id FROM models WHERE name = ?", (r["name"],)):
            continue
        execute(
            """INSERT INTO models(id, name, kind, provider, model, base_url, api_key,
                                  path, dim, note, created_at, updated_at)
               VALUES (?, ?, 'embedding', 'local', '', '', '', ?, ?, ?, ?, ?)""",
            (
                uuid.uuid4().hex[:12],
                r["name"],
                r["path"],
                r["dim"],
                r["note"] or "",
                r["created_at"],
                _now(),
            ),
        )
        migrated += 1
    if migrated:
        logger.info(f"已迁移 {migrated} 个旧版本地模型到统一模型表")
    return migrated
