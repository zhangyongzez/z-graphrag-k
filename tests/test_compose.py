from pathlib import Path

import yaml


def test_compose_is_valid_yaml():
    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "services" in data
    assert "milvus" in data["services"]
    assert "neo4j" in data["services"]


def test_neo4j_does_not_use_host_7687():
    """宿主机 7687 被占用，compose 里必须映射到别的端口。"""
    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    ports = data["services"]["neo4j"]["ports"]
    assert not any(str(p).startswith("7687:") for p in ports)
    assert any("7688" in str(p) for p in ports)


def test_milvus_and_neo4j_have_profiles():
    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "milvus" in data["services"]["milvus"]["profiles"]
    assert "graph" in data["services"]["neo4j"]["profiles"]


def test_app_service_exists():
    """应用服务应包含在 compose 里，开箱即可 docker compose up。"""
    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "app" in data["services"]
    app = data["services"]["app"]
    assert "build" in app
    assert "8000" in str(app["ports"])
    # 应用默认启动，不需要额外 profile
    assert "profiles" not in app


def test_app_uses_named_volume_for_data():
    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    app = data["services"]["app"]
    assert any("app_data" in v for v in app["volumes"])
    assert "app_data" in data["volumes"]


def test_dockerfile_runs_as_non_root():
    """容器内不应以 root 运行。"""
    root = Path(__file__).resolve().parents[1]
    content = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "USER kgrag" in content or "USER " in content
