"""跨 session 的 bridge 注册表（open -a 遗留优化项 3 —— 原型，未接线）

[实验改动点] 新增文件，不改动任何现有代码路径；回滚 = 删除本文件 +
tests/test_bridge_registry.py。

设计目标：多个 dpx session（-S work / default / ...）attach 同一个
user-data-dir 时复用同一个 bridge 子进程——inspect 模式每条 browser-level
WS 都要用户点一次 Allow，bridge 是"授权会话"的持有者，理应全局唯一。

文件布局（~/.dp_cli/bridges/）：

    <key>.json   注册项；key = sha256(resolved user_data_dir)[:16]
    <key>.lock   每键互斥文件锁（POSIX flock / Windows msvcrt）

注册项 schema::

    {
      "version": 1,
      "user_data_dir": "/abs/resolved/path",
      "real_port":  38081,        # 注册时 DevToolsActivePort 的端口
      "pid":        1234,          # bridge 子进程 pid
      "port":       40123,         # bridge 监听端口
      "created_at": "ISO8601",
      "sessions":   ["default"]    # 信息性：哪些 session 在用（不做引用计数）
    }

并发模型：``acquire_or_spawn`` 全程持有键级文件锁——包括 spawn_fn
（即 start_bridge，最坏 ~135s 等用户点 Allow）。语义：同一 profile 上
永远只有一个进程真 spawn，并发 open -a 的败者阻塞在锁上、拿到锁后读到
注册项直接复用，零额外交互。代价是败者最坏等一整轮授权——这是正确
取舍：并发 spawn 才会产生双授权框/双 bridge 的坏状态。

清理策略：注册项完全自校验（pid 活 + 端口开 + /healthz upstream +
real_port 与当前 DevToolsActivePort 一致）。stale 项在读路径顺手删除；
bridge 上游断自杀后端口即死，下一条命令自动重 spawn 并原子覆盖。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from dp_cli.logs import debug
from dp_cli.output import _write_err

IS_WINDOWS = sys.platform == 'win32'

_LOCK_WAIT_MSG = ('[dp] 另一个 dpx 进程正在为该浏览器启动 bridge '
                  '（可能在等您点击 Allow），等待其完成…')


def _bridges_dir() -> Path:
    """注册表目录；独立成函数便于测试 monkeypatch。"""
    d = Path.home() / '.dp_cli' / 'bridges'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key(user_data_dir: str | os.PathLike) -> str:
    """user_data_dir → 注册表键。resolve 消除 ~/../符号链接差异。"""
    resolved = str(Path(user_data_dir).expanduser().resolve())
    return hashlib.sha256(resolved.encode()).hexdigest()[:16]


def _entry_path(user_data_dir) -> Path:
    return _bridges_dir() / f'{_key(user_data_dir)}.json'


def _lock_path(user_data_dir) -> Path:
    return _bridges_dir() / f'{_key(user_data_dir)}.lock'


# ── 键级文件锁 ────────────────────────────────────────────────────────────
#
# flock 锁绑定"打开文件描述"：同一进程内两次独立 open() 也会互斥，
# 因此线程/进程并发都适用（测试用线程即可覆盖）。


@contextmanager
def _locked(user_data_dir, blocking_msg: bool = True):
    """持有该 profile 的互斥锁。先非阻塞试一次，被占则提示后阻塞等。

    败者在锁上等最坏 ~135s（胜者的授权窗口）；期间给用户一行提示，
    避免看起来像卡死。
    """
    lf = open(_lock_path(user_data_dir), 'a+b')
    try:
        if IS_WINDOWS:
            import msvcrt
            lf.seek(0)
            try:
                msvcrt.locking(lf.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                if blocking_msg:
                    _write_err(_LOCK_WAIT_MSG + '\n')
                deadline = time.monotonic() + 600
                while True:
                    try:
                        lf.seek(0)
                        msvcrt.locking(lf.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise TimeoutError('bridge registry lock 超时')
                        time.sleep(0.2)
            try:
                yield
            finally:
                lf.seek(0)
                msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            got = False
            try:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
            except OSError:
                pass
            if not got and blocking_msg:
                _write_err(_LOCK_WAIT_MSG + '\n')
            try:
                if not got:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    finally:
        lf.close()


def _write_json_atomic(path: Path, obj: dict) -> None:
    """tmp + os.replace：读者永远看到完整 JSON 或不存在。"""
    tmp = path.with_suffix(path.suffix + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                   encoding='utf-8')
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except Exception as e:
        debug(f'bridge 注册项 {path.name} 解析失败', e)
        return None


# ── 注册项校验 ────────────────────────────────────────────────────────────


def _default_validate(entry: dict, real_port: int | None) -> bool:
    """注册项是否指向一个仍可用的 bridge。

    四道闸（全部廉价、毫秒级）：
      1. pid 存活            —— is_bridge_alive
      2. 监听端口 TCP 可达    —— _port_open
      3. /healthz upstream   —— 上游 WS 没断（bridge 上游断会自杀，
                               但自杀与被读之间有时窗）
      4. real_port 一致      —— Chrome 重启换端口后旧 bridge 必死，
                               此项主要防 pid 复用造成的假命中
    """
    from dp_cli.bridge_manager import is_bridge_alive
    from dp_cli.session import _port_open, _bridge_upstream_alive

    pid, port = entry.get('pid'), entry.get('port')
    if not pid or not port:
        return False
    if not is_bridge_alive(int(pid)):
        return False
    if not _port_open('127.0.0.1', int(port)):
        return False
    if not _bridge_upstream_alive(int(port)):
        return False
    if real_port is not None and entry.get('real_port') != real_port:
        return False
    return True


def find_bridge(user_data_dir, real_port: int | None = None,
                validate_fn=None) -> dict | None:
    """查注册表；有效返回注册项 dict，无效/不存在返回 None 并清 stale 文件。

    注意：本函数**不持锁**，供 close/show 等只读路径用。spawn 决策必须走
    acquire_or_spawn（锁内再查）。
    """
    path = _entry_path(user_data_dir)
    entry = _read_json(path)
    if entry is None:
        return None
    ok = (validate_fn or _default_validate)(entry, real_port)
    if ok:
        return entry
    try:
        path.unlink()
    except OSError as e:
        debug(f'清理 stale bridge 注册项 {path.name} 失败', e)
    return None


def register_bridge(user_data_dir, pid: int, port: int,
                    real_port: int | None = None,
                    session_name: str | None = None) -> dict:
    """原子写入注册项（调用方应已持锁）。"""
    from datetime import datetime
    entry = {
        'version': 1,
        'user_data_dir': str(Path(user_data_dir).expanduser().resolve()),
        'real_port': real_port,
        'pid': int(pid),
        'port': int(port),
        'created_at': datetime.now().isoformat(),
        'sessions': [session_name] if session_name else [],
    }
    _write_json_atomic(_entry_path(user_data_dir), entry)
    return entry


def unregister_bridge(user_data_dir) -> bool:
    p = _entry_path(user_data_dir)
    if p.exists():
        p.unlink()
        return True
    return False


def acquire_or_spawn(user_data_dir, spawn_fn,
                     real_port: int | None = None,
                     session_name: str | None = None,
                     validate_fn=None):
    """锁内"查注册表 → 命中复用 / 未命中 spawn → 写注册表"。

    :param spawn_fn: ``fn(user_data_dir) -> (pid, port)``，接线时即
        ``bridge_manager.start_bridge``；测试注入假实现即可。
    :return: ``(pid, port, adopted)``；adopted=True 表示复用既有 bridge
    """
    with _locked(user_data_dir):
        entry = find_bridge(user_data_dir, real_port=real_port,
                            validate_fn=validate_fn)
        if entry is not None:
            _note_session(entry, session_name)
            return int(entry['pid']), int(entry['port']), True
        pid, port = spawn_fn(user_data_dir)
        entry = register_bridge(user_data_dir, pid, port,
                                real_port=real_port,
                                session_name=session_name)
        return int(entry['pid']), int(entry['port']), False


def _note_session(entry: dict, session_name: str | None) -> None:
    """把采用者 session 名补进注册项（best-effort，信息性字段）。"""
    if not session_name or session_name in entry.get('sessions', []):
        return
    try:
        entry.setdefault('sessions', []).append(session_name)
        _write_json_atomic(_entry_path(entry['user_data_dir']), entry)
    except Exception as e:
        debug('更新 bridge 注册项 sessions 失败（忽略）', e)


def iter_entries() -> list[dict]:
    """全部注册项（含 stale），供 `dpx bridge list/stop` 用。"""
    out = []
    for f in sorted(_bridges_dir().glob('*.json')):
        data = _read_json(f)
        if data is not None:
            data['_file'] = f.name
            out.append(data)
    return out
