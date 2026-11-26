"""一键启动脚本与需求覆盖检查。"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_start_scripts_exist():
    for name in ("start.py", "start.bat", "start.sh"):
        assert (ROOT / name).exists(), f"缺少 {name}"


def test_start_py_has_required_flags():
    src = (ROOT / "start.py").read_text(encoding="utf-8")
    ast.parse(src)  # 语法正确
    for flag in ("--port", "--host", "--no-browser", "--reload"):
        assert flag in src


def test_web_ui_has_all_pages():
    html = (ROOT / "src/kgrag/web/index.html").read_text(encoding="utf-8")
    for page in (
        "page-dashboard",
        "page-kb",
        "page-chat",
        "page-config",
        "page-models",
        "page-admin-dash",
        "page-users",
    ):
        assert f'id="{page}"' in html, f"缺少页面 {page}"


def test_loader_supports_required_formats():
    """需求 3.1：txt、word、ppt、excel、html、md、xml。"""
    from kgrag.ingest import SUPPORTED_SUFFIXES

    required = {
        ".txt",
        ".docx",
        ".pptx",
        ".xlsx",
        ".html",
        ".md",
        ".xml",
    }
    assert required <= SUPPORTED_SUFFIXES


def test_config_groups_cover_requirements():
    """需求 1.1.1：embedding / 大模型 / 向量库 都要可配置。"""
    from kgrag.settings import CONFIG_GROUPS

    keys = {g["key"] for g in CONFIG_GROUPS}
    assert {"embedding", "llm", "vectorstore"} <= keys
    assert "ad" in keys  # 需求 1.1.4

    emb = next(g for g in CONFIG_GROUPS if g["key"] == "embedding")
    field_keys = {f["key"] for f in emb["fields"]}
    assert "embedding_device" in field_keys  # CPU / GPU 模式
    assert "embedding_model_id" in field_keys  # 需求：模型改为下拉选择


def test_config_fields_have_help():
    """需求 5：配置页参数应有含义解释。"""
    from kgrag.settings import CONFIG_GROUPS

    missing = []
    for g in CONFIG_GROUPS:
        for f in g["fields"]:
            if not f.get("help"):
                missing.append(f"{g['key']}.{f['key']}")
    assert not missing, f"以下字段缺少说明: {missing}"


def test_model_fields_are_selects():
    """需求 3：配置页对模型只做下拉选择，不维护模型。"""
    from kgrag.settings import CONFIG_GROUPS

    model_fields = []
    for g in CONFIG_GROUPS:
        for f in g["fields"]:
            if f.get("type") == "model_select":
                model_fields.append(f)
    assert model_fields, "没有找到模型下拉字段"
    for f in model_fields:
        assert f["model_kind"] in ("llm", "embedding")
    # 旧的「手填模型名 / 路径 / 密钥」字段不应再出现在配置页
    all_keys = {f["key"] for g in CONFIG_GROUPS for f in g["fields"]}
    for gone in (
        "embedding_provider",
        "embedding_model",
        "embedding_local_path",
        "embedding_api_key",
        "llm_provider",
        "llm_api_key",
        "llm_api_base",
    ):
        assert gone not in all_keys, f"配置页不应再维护 {gone}"


def test_default_config_exists():
    """需求 3：应存在一份默认配置。"""
    from kgrag.settings import DEFAULT_CONFIG

    assert DEFAULT_CONFIG
    assert DEFAULT_CONFIG["llm_model_id"] == ""
    assert "retrieval_top_k" in DEFAULT_CONFIG
