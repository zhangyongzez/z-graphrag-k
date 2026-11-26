.PHONY: help install install-local test lint fmt doctor serve ingest clean

help:
	@echo "install       安装依赖（不含本地模型）"
	@echo "install-local 安装依赖（含 sentence-transformers / torch）"
	@echo "test          运行测试"
	@echo "lint          静态检查"
	@echo "fmt           格式化"
	@echo "doctor        自检"
	@echo "serve         启动 API 服务"
	@echo "ingest        入库：make ingest PATH=./docs"
	@echo "clean         清理缓存与构建产物"

install:
	uv pip install -e .

install-local:
	uv pip install -e ".[local,dev]"

test:
	uv run pytest tests/ -q

lint:
	uv run ruff check src tests
	uv run mypy src

fmt:
	uv run ruff format src tests
	uv run ruff check --fix src tests

doctor:
	uv run python -m kgrag.cli doctor

serve:
	uv run python -m kgrag.cli serve --reload

ingest:
	@test -n "$(PATH)" || (echo "用法: make ingest PATH=./docs" && exit 1)
	uv run python -m kgrag.cli ingest "$(PATH)"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
