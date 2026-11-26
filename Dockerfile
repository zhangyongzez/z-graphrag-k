# syntax=docker/dockerfile:1
# 应用镜像。默认不含 torch（走远程 embedding / LLM）。
# 需要本地模型时用 --build-arg INSTALL_LOCAL=1 并挂载模型目录。

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# 系统依赖：pypdf/docx 需要，unstructured 的 pdf 后端依赖 poppler
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖，利用层缓存
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

ARG INSTALL_LOCAL=0
RUN pip install --upgrade pip && \
    if [ "$INSTALL_LOCAL" = "1" ]; then \
        pip install ".[local]"; \
    else \
        pip install .; \
    fi

COPY . .

# 数据目录：容器内以非 root 用户运行，需可写
RUN mkdir -p /app/data /app/models && \
    useradd --create-home --shell /usr/sbin/nologin kgrag && \
    chown -R kgrag:kgrag /app/data /app/models
USER kgrag

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "kgrag.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
