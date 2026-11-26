#!/usr/bin/env python
"""一键启动脚本：自动准备环境 → 检查依赖 → 启动服务。

用法：
    python start.py                  # 默认启动，自动打开浏览器
    python start.py --no-browser     # 不打开浏览器
    python start.py --port 9000      # 指定端口
    python start.py --reload         # 开发模式

会依次完成：
1. 检查 Python 版本（>=3.11）
2. 首次运行时从 .env.example 生成 .env
3. 缺依赖时自动 pip install
4. 初始化数据目录 / SQLite
5. 启动 uvicorn，并打印访问地址与管理员账号
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_PYTHON = (3, 11)


def log(msg: str) -> None:
    print(f"\033[36m[kgrag]\033[0m {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"\033[33m[kgrag]\033[0m {msg}", flush=True)


def die(msg: str) -> None:
    print(f"\033[31m[kgrag]\033[0m {msg}", flush=True)
    sys.exit(1)


def check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        die(f"需要 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+，当前 {sys.version.split()[0]}")
    log(f"Python {sys.version.split()[0]}")


def ensure_env() -> None:
    env = ROOT / ".env"
    example = ROOT / ".env.example"
    if not env.exists() and example.exists():
        shutil.copyfile(example, env)
        log("已从 .env.example 生成 .env")
    elif not env.exists():
        env.write_text("", encoding="utf-8")


def load_env() -> None:
    """轻量读取 .env（不依赖 python-dotenv，保证在装依赖之前也能跑）。"""
    env = ROOT / ".env"
    if not env.exists():
        return
    for raw in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def check_deps() -> None:
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        log("缺少依赖，正在安装...")
        cmd = [sys.executable, "-m", "pip", "install", "-e", "."]
        if subprocess.run(cmd, cwd=ROOT).returncode != 0:
            die("依赖安装失败，请手动执行：pip install -e .")


def prepare_dirs() -> None:
    data_dir = Path(os.environ.get("DATA_DIR", ROOT / "data"))
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    for d in (data_dir, data_dir / "milvus", data_dir.parent / "models"):
        d.mkdir(parents=True, exist_ok=True)
    log(f"数据目录: {data_dir}")


def print_banner(host: str, port: int) -> None:
    url = f"http://{'localhost' if host in ('0.0.0.0', '::') else host}:{port}"
    admin = os.environ.get("ADMIN_USERNAME", "admin")
    has_pwd = bool(os.environ.get("ADMIN_PASSWORD"))
    print()
    print("  " + "=" * 52)
    print("   z-graphrag-k 知识库系统已启动")
    print("  " + "=" * 52)
    print(f"   访问地址 : {url}")
    print(f"   接口文档 : {url}/docs")
    print(f"   管理员   : {admin}"
          + ("" if has_pwd else "  （密码见首次启动日志，或用 ADMIN_PASSWORD 指定）"))
    print("  " + "=" * 52)
    print()


def open_browser_later(url: str, delay: float = 2.0) -> None:
    def _open() -> None:
        time.sleep(delay)
        with contextlib.suppress(Exception):
            webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="z-graphrag-k 一键启动")
    parser.add_argument("--host", default=None, help="监听地址")
    parser.add_argument("--port", type=int, default=None, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--skip-deps", action="store_true", help="跳过依赖检查")
    args = parser.parse_args()

    check_python()
    ensure_env()
    load_env()
    if not args.skip_deps:
        check_deps()
    prepare_dirs()

    sys.path.insert(0, str(ROOT / "src"))
    host = args.host or os.environ.get("APP_HOST", "0.0.0.0")
    port = args.port or int(os.environ.get("APP_PORT", "8000"))

    print_banner(host, port)
    if not args.no_browser:
        url = f"http://{'localhost' if host in ('0.0.0.0', '::') else host}:{port}"
        open_browser_later(url)

    import uvicorn

    uvicorn.run(
        "kgrag.api.app:app",
        host=host,
        port=port,
        reload=args.reload,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止")
