"""统一的日志初始化模块。

以 loguru 为中心，配置文件日志（带轮转）、审计日志与可选的 UI 实时回调。
所有入口（GUI / CLI / 测试）都应调用 setup_logging() 一次来完成初始化。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from loguru import logger

from config_manager import get_user_config_dir

# 日志文件统一放在用户配置目录下
_LOG_DIR = get_user_config_dir()
APP_LOG_PATH = _LOG_DIR / "file.log"
AUDIT_LOG_PATH = _LOG_DIR / "awin_audit.jsonl"

_initialized = False
_ui_sink_id: int | None = None

# 自定义 SUCCESS 级别（介于 INFO=20 和 WARNING=30 之间）
SUCCESS_LEVEL_NO = 25
SUCCESS_LEVEL_NAME = "SUCCESS"


def setup_logging(level: str = "INFO") -> None:
    """初始化全局日志配置（幂等）。

    - 移除 loguru 默认的 stderr sink（避免与 GUI 场景冲突）
    - 注册自定义 SUCCESS 级别
    - 添加文件日志 sink：每日一份，保留最近 7 天，UTF-8 编码
    - 添加结构化审计日志 sink：仅记录带 audit=True 的日志，JSONL 格式
    """
    global _initialized
    if _initialized:
        return

    # 注册自定义 SUCCESS 级别（幂等：已存在则跳过）
    try:
        logger.level(SUCCESS_LEVEL_NAME)
    except ValueError:
        logger.level(SUCCESS_LEVEL_NAME, no=SUCCESS_LEVEL_NO, color="<green>", icon="✅")

    # 移除默认 stderr sink，统一由我们自己配置
    logger.remove()

    # 文件日志：普通运行日志，每日零点轮转，保留最近 7 天
    logger.add(
        APP_LOG_PATH,
        level=level,
        rotation="00:00",
        retention="7 days",
        encoding="utf-8",
        enqueue=True,  # 线程/进程安全，配合 GUI 线程
        backtrace=True,
        diagnose=False,
    )

    # 同时在控制台输出一份（开发/CLI 模式下可见）
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | "
        "<level>{level:<7}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>",
        enqueue=True,
    )

    # 结构化审计日志：仅记录带 audit=True 的事件，同样每日轮转、保留 7 天
    logger.add(
        AUDIT_LOG_PATH,
        level="INFO",
        serialize=True,
        filter=_audit_filter,
        rotation="00:00",
        retention="7 days",
        encoding="utf-8",
        enqueue=True,
    )

    _initialized = True


def _audit_filter(record: dict) -> bool:
    """过滤出标记为 audit=True 的日志记录。"""
    return bool(record["extra"].get("audit"))


def register_ui_sink(callback: Callable[[str, str, str], None]) -> int:
    """注册一个 UI 实时日志回调，返回 sink id（用于移除）。

    callback 签名: callback(timestamp: str, level: str, message: str) -> None
    - timestamp: "HH:MM:SS" 格式
    - level: "info" / "success" / "warning" / "error"
    - message: 日志消息文本
    """
    # 移除之前注册的 UI sink（避免重复）
    global _ui_sink_id
    if _ui_sink_id is not None:
        try:
            logger.remove(_ui_sink_id)
        except Exception:
            pass

    def _sink(message: object) -> None:
        # loguru 传进来的是 Message 对象
        record = message.record  # type: ignore[attr-defined]
        timestamp = record["time"].strftime("%H:%M:%S")
        level = record["level"].name.lower()
        # 把 loguru 级别映射到 UI 级别
        level_map = {
            "info": "info",
            "success": "success",
            "warning": "warning",
            "warn": "warning",
            "error": "error",
            "critical": "error",
            "debug": "info",
            "trace": "info",
        }
        ui_level = level_map.get(level, "info")
        try:
            callback(timestamp, ui_level, record["message"])
        except Exception:
            # UI 回调出错不能影响日志流程
            pass

    sink_id = logger.add(_sink, level="INFO", format="{message}")
    _ui_sink_id = sink_id
    return sink_id


def unregister_ui_sink(sink_id: int | None = None) -> None:
    """注销 UI 日志回调。"""
    global _ui_sink_id
    target = sink_id if sink_id is not None else _ui_sink_id
    if target is None:
        return
    try:
        logger.remove(target)
    except Exception:
        pass
    if sink_id is None or sink_id == _ui_sink_id:
        _ui_sink_id = None


def get_log_file_path() -> Path:
    """返回当前使用的日志文件路径。"""
    return APP_LOG_PATH
