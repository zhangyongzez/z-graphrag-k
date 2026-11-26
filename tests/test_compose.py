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
