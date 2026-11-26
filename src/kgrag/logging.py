"""统一日志：loguru + 标准 logging 桥接。"""

from __future__ import annotations

import contextlib
import logging
import sys
from pathlib import Path

from loguru import logger as _loguru_logger

from kgrag.config import get_settings

_configured = False


class _InterceptHandler(logging.Handler):
    """把标准库 logging 转发到 loguru。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = _loguru_logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        _loguru_logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


class _SafeStderrSink:
    """延迟解析 sys.stderr，写入失败时静默丢弃。

    pytest / 解释器退出阶段 stderr 可能已被关闭，直接持有 stream 会让
    loguru 在 emit 时抛 ``ValueError: I/O operation on closed file``。

    .. important::

        **Windows 下必须在后台线程写控制台**

        如果这个 sink 在事件循环线程里同步写 ``sys.stderr``，一旦控制台
        停止消费输出（最常见：在 cmd 窗口里点鼠标选中文字，触发
        QuickEdit 选择模式），``sys.stderr.write()`` 会永久阻塞，
        整个 uvicorn 事件循环随之停摆——表现为服务「假死」，所有请求
        超时、CPU 占用 0%、日志不再更新。

        因此注册时统一使用 ``enqueue=True``，把真正的写操作放到
        loguru 的后台线程；控制台卡住最多撑爆队列，不会拖死服务。
    """

    def write(self, message: str) -> None:
        stream = sys.stderr
        if stream is None:  # pythonw / 无控制台
            return
        with contextlib.suppress(ValueError, OSError):
            stream.write(message)
            stream.flush()

    def __call__(self, message) -> None:
        self.write(str(message))

    def flush(self) -> None:
        stream = sys.stderr
        if stream is None:
            return
        with contextlib.suppress(ValueError, OSError):
            stream.flush()


def _setup() -> None:
    global _configured
    if _configured:
        return
    s = get_settings()

    _loguru_logger.remove()
    _loguru_logger.add(
        _SafeStderrSink(),
        level=s.log_level.upper(),
        format=(
            "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
        ),
        colorize=True,
        backtrace=False,
        diagnose=False,
        catch=True,  # pytest/解释器退出时 stderr 可能已关闭
        # 关键：放到后台线程写。控制台卡住（Windows QuickEdit 选中文字）
        # 时不会阻塞事件循环，避免服务假死。
        enqueue=True,
    )

    log_dir = Path(s.data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _loguru_logger.add(
        log_dir / "app.log",
        level=s.log_level.upper(),
        rotation="20 MB",
        retention="14 days",
        encoding="utf-8",
        enqueue=True,
        backtrace=False,
        diagnose=False,
        catch=True,  # 退出阶段写入已关闭文件时不抛异常
    )

    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore"):
        lg = logging.getLogger(name)
        lg.handlers = [_InterceptHandler()]
        lg.propagate = False

    _configured = True


def get_logger(name: str = __name__):
    _setup()
    return _loguru_logger.bind(name=name)


logger = get_logger("kgrag")
