# -*- coding:utf-8 -*-
"""daemon socket 化：普通 dpx <cmd> 免 Python 启动+attach 税。

- unix socket：~/.dp_cli/daemon/{session}.sock（不放 TMPDIR——重启即失效
  易留僵尸；HOME 下稳定可定位）+ {session}.session 元数据
  （pid/socket/started_at/owner）
- 客户端：连 socket → ENOENT/ECONNREFUSED → spawn `python -m dp_cli.daemon`
  （setsid detached）→ retry once
- 超时分层：socket read 超时 = 命令 --timeout + 15s margin
  （防外层先超时内层仍在跑产生重发）
- idle 自退：headless 会话 1h 无活动退出；user_connected(attached) 豁免
- 协议：复用 serve.py 的 JSONL（socket 每连接一行请求一行响应）
- opt-in：`DPX_DAEMON=1` 或 `dpx daemon-start` 建 socket 后自动路由
"""
import json
import os
import socket as _socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import click

from dp_cli.output import ok, error, debug

_DIR = Path.home() / '.dp_cli' / 'daemon'
_IDLE_TTL = 3600          # headless 空闲自退秒数
_SPAWN_WAIT = 8.0         # spawn 后等 socket 就绪
_TIMEOUT_MARGIN = 15      # read 超时 margin（秒）
_SKIP_CMDS = {'serve', 'sh', 'batch', 'daemon-start', 'daemon-status',
              'daemon-stop', 'install', 'catalog', 'mcp'}
# record-* 精确枚举（进程内录制状态，daemon 不可转发）——
# 不用 record- 前缀匹配，避免误伤未来同名前缀的非进程内命令
_RECORD_CMDS = {'record-start', 'record-stop', 'record-show',
                'record-clear', 'record-status', 'record-export'}


def _skipped(cmd: str) -> bool:
    return cmd in _SKIP_CMDS or cmd in _RECORD_CMDS


_HAS_UNIX_SOCK = hasattr(_socket, 'AF_UNIX')   # Windows 无 AF_UNIX


def _dpx_version() -> str:
    try:
        import importlib.metadata as m
        return m.version('dp-cli')
    except Exception:
        return 'dev'


_VER_WARNED = set()   # 每进程每 session 只警告一次


def _version_probe(session: str) -> None:
    """连接成功后探测 daemon 版本——不匹配仅 stderr 警告不拒绝。"""
    if session in _VER_WARNED:
        return
    _VER_WARNED.add(session)
    try:
        meta = json.loads(
            _meta_path(session).read_text(encoding='utf-8'))
        dv = meta.get('version')
        cv = _dpx_version()
        if dv and dv != cv:
            print(f'daemon v{dv} vs dpx v{cv}——建议 '
                  f'dpx daemon-stop -S {session} 后重开',
                  file=sys.stderr)
    except Exception:
        pass


def _sock_path(session: str) -> Path:
    return _DIR / f'{session}.sock'


def _meta_path(session: str) -> Path:
    return _DIR / f'{session}.session'


def _daemon_ready(session: str) -> bool:
    """daemon 就绪标记——Unix 看 .sock 文件，TCP 平台看 meta。"""
    if _HAS_UNIX_SOCK:
        return _sock_path(session).exists()
    return _meta_path(session).exists()


def _connect_endpoint(session: str):
    """连接端点：POSIX→.sock 路径；Windows→('127.0.0.1', port)。

    TCP 端口写进 meta 文件（{session}.session 的 "port" 字段）。
    """
    if _HAS_UNIX_SOCK:
        return str(_sock_path(session))
    try:
        meta = json.loads(_meta_path(session).read_text(encoding='utf-8'))
        return ('127.0.0.1', int(meta['port']))
    except Exception:
        return ('127.0.0.1', 0)


def _session_of(argv: list) -> str:
    for i, a in enumerate(argv):
        if a in ('--session', '-S') and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith('--session='):
            return a.split('=', 1)[1]
    return 'default'


def _cmd_timeout(argv: list) -> float:
    """命令 --timeout（秒）+ margin → socket read 超时。"""
    t = 60.0
    for i, a in enumerate(argv):
        if a == '--timeout' and i + 1 < len(argv):
            try:
                t = float(argv[i + 1])
            except ValueError:
                pass
        elif a.startswith('--timeout='):
            try:
                t = float(a.split('=', 1)[1])
            except ValueError:
                pass
    return t + _TIMEOUT_MARGIN


# ── 客户端 ─────────────────────────────────────────────────

def _send_recv(session: str, payload: dict, read_timeout: float):
    """一次 socket 往返 → dict 响应；异常返回 ('err', exc)。"""
    try:
        ep = _connect_endpoint(session)
        if _HAS_UNIX_SOCK:
            s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        else:
            s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(read_timeout)
        s.connect(ep)
        s.sendall((json.dumps(payload, ensure_ascii=False) + '\n').encode())
        buf = b''
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
            if b'\n' in buf:
                break
        s.close()
        line = buf.split(b'\n', 1)[0]
        _version_probe(session)
        return json.loads(line.decode('utf-8'))
    except (OSError, json.JSONDecodeError) as e:
        return ('err', e)


def _acquire_spawn_lock(path: Path, timeout: float = 10.0):
    """跨平台排他锁（contextmanager）。

    Unix：fcntl.flock；无 fcntl 平台（Windows）：O_CREAT|O_EXCL
    原子创建 lock 文件 + 轮询重试——原子性足够防并发孤儿。
    """
    import contextlib

    try:
        import fcntl
    except ImportError:
        fcntl = None

    @contextlib.contextmanager
    def _flock():
        f = path.open('w')
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            f.close()

    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == 'posix':
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
        try:    # Windows：OpenProcess 探测
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        except Exception:
            return False

    @contextlib.contextmanager
    def _excl():
        deadline = time.monotonic() + timeout
        fd = None
        while fd is None:
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL
                             | os.O_WRONLY)
            except FileExistsError:
                # stale 检测：锁文件记 pid，持有者死亡即回收
                try:
                    lp = int(path.read_text().strip() or '-1')
                except (OSError, ValueError):
                    lp = -1
                # 刚创建的锁 pid 还没写——1s 内不算 stale
                if lp <= 0:
                    try:
                        fresh = (time.time()
                                 - path.stat().st_mtime) < 1.0
                    except OSError:
                        fresh = False
                    alive = fresh
                else:
                    alive = _pid_alive(lp)
                if not alive:
                    try:
                        path.unlink()
                    except OSError:
                        pass
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f'spawn lock 超时: {path}')
                time.sleep(0.05)
        try:
            os.write(fd, str(os.getpid()).encode())
            yield
        finally:
            os.close(fd)
            try:
                path.unlink()
            except OSError:
                pass

    return _flock() if fcntl is not None else _excl()


def _spawn_kwargs() -> dict:
    """daemon 子进程脱离参数——POSIX setsid / Windows DETACHED。"""
    if os.name == 'posix':
        return {'start_new_session': True}
    flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | \
        getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    return {'creationflags': flags} if flags else {}


def _spawn_daemon(session: str) -> bool:
    """spawn detached daemon，等 socket 就绪。

    {session}.lock flock 覆盖「查 socket + spawn」区间——多并发只起
    一次（与 bridge_registry.acquire_or_spawn 同款），败者持锁后
    重查 socket 直接 adopt，不产生孤儿 daemon。
    """
    _DIR.mkdir(parents=True, exist_ok=True)
    try:
        with _acquire_spawn_lock(_DIR / f'{session}.lock'):
            if _daemon_ready(session):
                return True          # 前一轮已起好 → adopt
            try:
                subprocess.Popen(
                    [sys.executable, '-m', 'dp_cli.daemon',
                     '--session', session],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, close_fds=True,
                    **_spawn_kwargs())
            except OSError:
                return False
            deadline = time.monotonic() + _SPAWN_WAIT
            while time.monotonic() < deadline:
                if _daemon_ready(session):
                    return True
                time.sleep(0.15)
            return False
    except TimeoutError as e:
        error('获取 daemon spawn 锁超时', code='DAEMON_LOCK_TIMEOUT',
              detail=str(e))
        return False


def try_daemon(argv: list):
    """命中 daemon 路由则执行并返回退出码；否则返回 None 走本地。

    路由条件：DPX_DAEMON=1，或 {session}.sock 已存在（daemon-start 建过）。
    跳过 daemon 自身命令/嵌套执行器/元命令。
    """
    if not argv:
        return None
    # 取首个"非 flag 且非 flag 值"的参数作命令名——--session/-S 的值要跳过
    _VAL_FLAGS = {'--session', '-S', '--timeout', '--output', '-o'}
    cmd = None
    skip_next = False
    for a in argv:
        if skip_next:
            skip_next = False
            continue
        if a.startswith('-'):
            if a in _VAL_FLAGS and '=' not in a:
                skip_next = True
            continue
        cmd = a
        break
    if cmd is None or _skipped(cmd):
        return None
    session = _session_of(argv)
    enabled = (os.environ.get('DPX_DAEMON') == '1'
               or _daemon_ready(session))
    if not enabled:
        return None
    payload = {'argv': argv}
    rt = _cmd_timeout(argv)
    r = _send_recv(session, payload, rt) if _daemon_ready(session)         else ('err', None)
    if isinstance(r, tuple):
        # ENOENT/ECONNREFUSED → spawn → retry once
        if not _spawn_daemon(session):
            return None  # 起不来走本地，不阻塞
        r = _send_recv(session, payload, rt)
    if isinstance(r, tuple) or r is None:
        return None     # daemon 仍不通 → 本地兜底
    sys.stdout.write(json.dumps(r.get('result', r), ensure_ascii=False) + '\n')
    sys.stdout.flush()
    return int(r.get('exit', 0))


# ── 服务端 ─────────────────────────────────────────────────

def _handle_conn(conn, session):
    try:
        buf = b''
        conn.settimeout(60)
        while b'\n' not in buf:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
        raw = buf.split(b'\n', 1)[0].strip()
        from dp_cli.commands.serve import (_parse_request, _accepts_session,
                                           _FORBIDDEN, _GLOBAL_FLAGS)
        from dp_cli.commands.batch import _invoke, _invalid
        argv, req_id, err = _parse_request(
            raw.decode('utf-8', errors='replace')) if raw else (None, None, '空行')
        if err:
            code, result = _invalid(err)
        else:
            cmd_name = next((a for a in argv if a not in _GLOBAL_FLAGS), None)
            # daemon-stop 先于 _SKIP_CMDS 判定（否则永远到不了退出分支）
            if cmd_name == 'daemon-stop':
                code, result = 0, {'status': 'ok', 'message': 'daemon 退出中'}
            elif cmd_name in _FORBIDDEN or _skipped(cmd_name):
                code, result = _invalid(f'daemon 内不允许 {cmd_name}')
            else:
                if ('--session' not in argv and '-S' not in argv
                        and _accepts_session(cmd_name)):
                    argv = argv + ['--session', session]
                # netlog 前置武装：首条导航命令的 Network 事件
                # 才能被捕获（后置会丢该命令全程流量）
                try:
                    from dp_cli.commands.serve import _maybe_arm_netlog
                    _maybe_arm_netlog(session)
                except Exception:
                    pass
                code, result = _invoke(argv)
        out = {'exit': code, 'result': result}
        if req_id is not None:
            out['id'] = req_id
        conn.sendall((json.dumps(out, ensure_ascii=False) + '\n').encode())
        if argv and argv[0] == 'daemon-stop':
            raise SystemExit(0)
    except SystemExit:
        raise
    except Exception:
        try:
            conn.sendall(b'{"exit":1,"result":{"status":"error"}}\n')
        except OSError:
            pass
    finally:
        conn.close()


def _idle_watcher(sock_path, meta_path, stop_ev, session):
    """headless 会话 1h 无活动自退；user_connected 豁免。"""
    while not stop_ev.wait(30):
        try:
            from dp_cli.session import load_session
            sess = load_session(session) or {}
            if sess.get('user_connected'):
                continue
            idle = time.time() - _state['last_activity']
            if idle > _IDLE_TTL:
                debug(f'daemon 空闲 {idle:.0f}s > {_IDLE_TTL}s，自退')
                os._exit(0)
        except Exception:
            pass


_state = {'last_activity': time.time()}


def run_daemon(session: str):
    """daemon 主循环：bind socket → 每连接一个 JSONL 请求 → 应答。"""
    _DIR.mkdir(parents=True, exist_ok=True)
    sock_path = _sock_path(session)
    meta_path = _meta_path(session)
    for p in (sock_path, meta_path):
        try:
            p.unlink()
        except OSError:
            pass
    if _HAS_UNIX_SOCK:
        srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        srv.bind(str(sock_path))
        transport = 'unix'
        endpoint = str(sock_path)
    else:
        srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        srv.bind(('127.0.0.1', 0))
        transport = 'tcp'
        endpoint = f"127.0.0.1:{srv.getsockname()[1]}"
    srv.listen(16)
    if _HAS_UNIX_SOCK:
        try:
            os.chmod(sock_path, 0o600)
        except OSError:
            pass
    meta_path.write_text(json.dumps({
        'pid': os.getpid(), 'socket': str(sock_path),
        'transport': transport, 'endpoint': endpoint,
        'port': srv.getsockname()[1] if transport == 'tcp' else None,
        'session': session, 'started_at': time.time(),
        'owner': os.environ.get('USER') or os.environ.get('USERNAME', '')}, ensure_ascii=False))
    stop_ev = threading.Event()
    threading.Thread(target=_idle_watcher,
                     args=(sock_path, meta_path, stop_ev, session),
                     daemon=True).start()
    try:
        while True:
            conn, _ = srv.accept()
            _state['last_activity'] = time.time()
            try:
                _handle_conn(conn, session)
            except SystemExit:
                break
            except Exception:
                pass
    finally:
        srv.close()
        for p in (sock_path, meta_path):
            try:
                p.unlink()
            except OSError:
                pass
    return 0


def _status(session: str):
    meta_p = _meta_path(session)
    sock_p = _sock_path(session)
    if not sock_p.exists():
        ok({'running': False, 'session': session})
        return
    meta = {}
    try:
        meta = json.loads(meta_p.read_text(encoding='utf-8'))
    except Exception:
        pass
    pid = meta.get('pid')
    alive = False
    if pid:
        try:
            os.kill(int(pid), 0)
            alive = True
        except (OSError, ValueError):
            pass
    ok({'running': alive, 'session': session, 'pid': pid,
        'socket': str(sock_p), 'started_at': meta.get('started_at'),
        'version': meta.get('version'),
        'owner': meta.get('owner')})


def register(cli):
    from dp_cli.commands._utils import session_option  # 惰性避环
    @cli.command('daemon-start')
    @session_option
    def daemon_start(session):
        """启动 session 常驻 daemon（后续 dpx 命令走 socket 免启动税）。"""
        if _sock_path(session).exists():
            ok({'session': session}, msg='daemon 已在运行')
            return
        if _spawn_daemon(session):
            ok({'session': session, 'socket': str(_sock_path(session))},
               msg='daemon 已启动（后续命令自动走 socket）')
        else:
            error('daemon 启动失败', code='DAEMON_FAILED')

    @cli.command('daemon-status')
    @session_option
    def daemon_status(session):
        """查看 session daemon 运行状态（pid/socket/uptime）。"""
        _status(session)

    @cli.command('daemon-stop')
    @session_option
    def daemon_stop(session):
        """停止 session daemon（socket 通知退出，或按元数据 kill）。"""
        sock = _sock_path(session)
        if not sock.exists():
            ok({'stopped': False}, msg='daemon 未在运行')
            return
        r = _send_recv(session, {'argv': ['daemon-stop']}, 10)
        if isinstance(r, dict):
            ok({'stopped': True}, msg='daemon 已停止')
            return
        meta_p = _meta_path(session)
        try:
            pid = json.loads(meta_p.read_text()).get('pid')
            os.kill(int(pid), 15)
            ok({'stopped': True, 'pid': pid}, msg='daemon 已 SIGTERM')
        except Exception:
            try:
                sock.unlink(); meta_p.unlink()
            except OSError:
                pass
            ok({'stopped': True}, msg='清理了失效 socket 文件')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--session', default='default')
    args = ap.parse_args()
    sys.exit(run_daemon(args.session))
