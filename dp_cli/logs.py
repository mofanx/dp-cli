# -*- coding:utf-8 -*-
"""dp-cli 全局 debug 日志通道（零依赖，避免循环导入）。

启用方式（任一）：
  - CLI 全局开关: dp -v/--verbose <command>
  - 环境变量: DP_DEBUG=1（或 true/yes/on）

约定：debug 输出一律走 stderr，不污染 stdout 的 JSON 契约。
容错路径（except: pass）应改用 debug() 记录而非静默吞掉，
便于排障时通过 DP_DEBUG=1 还原现场。
"""
import os
import sys

_DEBUG = os.environ.get('DP_DEBUG', '').lower() in ('1', 'true', 'yes', 'on')


def set_debug(enabled: bool = True) -> None:
    """启用/关闭 debug 日志（由 CLI --verbose 开关调用）。"""
    global _DEBUG
    _DEBUG = bool(enabled)


def is_debug() -> bool:
    return _DEBUG


def debug(msg: str, exc: BaseException = None) -> None:
    """debug 级日志：启用时输出到 stderr，否则无开销返回。

    :param msg: 日志内容
    :param exc: 可选异常对象，附加异常类型与消息
    """
    if not _DEBUG:
        return
    if exc is not None:
        msg = f'{msg}: {type(exc).__name__}: {exc}'
    print(f'[dp:debug] {msg}', file=sys.stderr)
