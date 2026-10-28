# -*- coding:utf-8 -*-
"""daemon socket 化：普通 dpx <cmd> 免 Python 启动+attach 税。

- unix socket：~/.dp_cli/daemon/{session}.sock（不放 TMPDIR——重启即失效
  易留僵尸；HOME 下稳定可定位；路径超 AF_UNIX sun_path 上限时例外，
  退 TMPDIR/dpx-<hash>.sock 短前缀，见 _sock_path）+ {session}.session
  元数据（pid/socket/started_at/owner）
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

from dp_cli.output import ok, error, debug, _write_out

_IDLE_TTL = 3600          # headless 空闲自退秒数
_IDLE_TTL_ATTACHED = 6 * 3600   # attached(user_connected) 会话宽限 TTL——
                              # 重建 attach 需重新点 Allow，频繁自退体验差；
                              # 但浏览器端口已死则豁免无依据，回落普通 TTL
_WATCH_TICK = 30          # idle watcher 巡检间隔（秒）
_SPAWN_WAIT = 8.0         # spawn 后等 socket 就绪
_TIMEOUT_MARGIN = 15      # read 超时 margin（秒）
_MAX_CONN = 32            # 并发处理闸——处理中连接数上限；
                          # 慢请求堆积撑爆 fd 的 DoS 面在此收口
_SKIP_CMDS = {'serve', 'sh', 'batch', 'daemon-start', 'daemon-status',
              'daemon-stop', 'install', 'catalog', 'mcp'}
# record-* 精确枚举（进程内录制状态，daemon 不可转发）——
# 不用 record- 前缀匹配，避免误伤未来同名前缀的非进程内命令
_RECORD_CMDS = {'record-start', 'record-stop', 'record-show',
                'record-clear', 'record-status', 'record-export'}


def _skipped(cmd: str) -> bool:
    return cmd in _SKIP_CMDS or cmd in _RECORD_CMDS


def _dir() -> Path:
    """daemon 状态目录——惰性求值（import 期固化会让 HOME 隔离失效，
    同 session._session_dir 教训）。"""
    return Path.home() / '.dp_cli' / 'daemon'


_HAS_UNIX_SOCK = hasattr(_socket, 'AF_UNIX')   # Windows 无 AF_UNIX


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == 'posix':
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:    # Windows：OpenProcess+GetExitCodeProcess——
        # 死 PID 也能拿到句柄（exit code≠STILL_ACTIVE）
        import ctypes
        return _win_pid_alive(ctypes.windll.kernel32, pid)
    except Exception:
        # 回退 tasklist 兜底（ctypes 失败时仍别误判活）
        try:
            import subprocess as _sp
            r = _sp.run(['tasklist', '/FI', f'PID eq {pid}'],
                        capture_output=True, text=True, timeout=3)
            return str(pid) in r.stdout
        except Exception:
            return False


def _win_pid_alive(k32, pid: int) -> bool:
    """kernel32 注入 seam（可测）：OpenProcess+GetExitCodeProcess。"""
    import ctypes
    h = k32.OpenProcess(0x0400, False, pid)   # PROCESS_QUERY_INFORMATION
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == 259            # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


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
            from dp_cli.output import _write_err
            _write_err(f'daemon v{dv} vs dpx v{cv}——建议 '
                       f'dpx daemon-stop -S {session} 后重开\n')
    except Exception:
        pass


_SUN_PATH_LIMIT = 100   # AF_UNIX sun_path 安全字节上限——
                        # Linux 108B/macOS 104B，留余量统一阈值


def _sock_path(session: str) -> Path:
    """POSIX sock 路径——超 sun_path 上限退 TMPDIR 短前缀。

    HOME 深路径（长用户名/网络 home/深 TMPDIR 测试基建）下
    ~/.dp_cli/daemon/{session}.sock 可超 AF_UNIX sun_path 上限
    （Linux 108B/macOS 104B），bind/connect 抛 'AF_UNIX path too
    long'——超限退 tempfile.gettempdir()/dpx-<sha1(session+目录)
    [:8]>.sock：纯函数按 session 可重建，client connect/server
    bind/stale 清理全走本函数天然同决策；不同 HOME 目录不同哈希
    不互踩。meta 文件无 sun_path 限制仍留 _dir() 原路径。
    Windows（无 AF_UNIX）不走本判定，路径形态不变。"""
    p = _dir() / f'{session}.sock'
    if not _HAS_UNIX_SOCK \
            or len(os.fsencode(str(p))) <= _SUN_PATH_LIMIT:
        return p
    import hashlib
    import tempfile
    h = hashlib.sha1(f'{session}|{p.parent}'
                     .encode('utf-8')).hexdigest()[:8]
    return Path(tempfile.gettempdir()) / f'dpx-{h}.sock'


def _meta_path(session: str) -> Path:
    return _dir() / f'{session}.session'


def _meta_sock_path(session: str):
    """meta.sock_path——server 实际 bind 的权威 sock 路径。

    深 HOME 退 TMPDIR 短前缀时，client/server 两侧 TMPDIR 不同
    会让 _sock_path 规则算出的路径≠server 实际 bind 处而失联；
    meta 落盘的 sock_path 字段是 client 侧唯一权威源。
    老 meta 无此字段/读取失败 → None（回退规则路径，向后兼容）。"""
    try:
        meta = json.loads(
            _meta_path(session).read_text(encoding='utf-8'))
        sp = meta.get('sock_path')
        return str(sp) if sp else None
    except Exception:
        return None


def _sock_path_eff(session: str) -> Path:
    """探活/清理用权威 sock 路径：meta.sock_path 优先（server 实际
    bind 处），老 meta 无字段回退 _sock_path 规则路径。"""
    mp = _meta_sock_path(session)
    return Path(mp) if mp else _sock_path(session)


def _unix_connect_eps(session: str):
    """client connect 候选端点：_sock_path 规则路径优先尝试，
    连不上时 meta.sock_path 兜底；meta 无字段/与规则一致→仅规则
    路径（向后兼容、行为不变）。"""
    eps = [str(_sock_path(session))]
    mp = _meta_sock_path(session)
    if mp and mp not in eps:
        eps.append(mp)
    return eps


def _tcp_alive(session: str) -> bool:
    """TCP 探活：meta 端口 connect_ex（Windows 分流用）。"""
    try:
        meta = json.loads(
            _meta_path(session).read_text(encoding='utf-8'))
        port = int(meta.get('port') or 0)
        if not port:
            return False
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(0.5)
        ok_c = s.connect_ex(('127.0.0.1', port)) == 0
        s.close()
        return ok_c
    except Exception:
        return False


def _meta_pid_alive(session: str) -> bool:
    """meta pid 存活检测（复用 _pid_alive 跨平台）。"""
    try:
        meta = json.loads(
            _meta_path(session).read_text(encoding='utf-8'))
        return _pid_alive(int(meta.get('pid') or -1))
    except Exception:
        return False


def _unix_alive(session: str) -> bool:
    """POSIX socket 探活：connect_ex .sock（对齐 _tcp_alive）。

    死 daemon 的 .sock 残留会让仅看 exists() 的调用方白等超时——
    ECONNREFUSED/ENOENT 即判死。候选路径 meta.sock_path 权威源
    优先、规则路径兜底（两侧 TMPDIR 不同时规则路径可能 ≠ 实际
    bind 处）。spawn 窗口（bind 后 meta 落盘前）的误杀由
    _spawn_fresh 的 mtime 兜底，见 _daemon_ready。"""
    paths = []
    mp = _meta_sock_path(session)
    if mp:
        paths.append(mp)
    rule = str(_sock_path(session))
    if rule not in paths:
        paths.append(rule)
    for p in paths:
        try:
            s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            s.settimeout(0.5)
            try:
                if s.connect_ex(p) == 0:
                    return True
            finally:
                s.close()
        except OSError:
            continue        # 单路径构造/连接异常不挡兜底路径
    return False


def _spawn_fresh(session: str) -> bool:
    """spawn 窗口兜底（POSIX）：.sock 已 bind 但 meta 未落盘
    （listen→meta write 间隙）且探活刚被拒绝——sock mtime 在
    _SPAWN_WAIT 内视为活，防 _cleanup_stale 误删新 daemon 标记。"""
    if _meta_path(session).exists():
        return False                       # meta 在则探活已裁决
    try:
        return (time.time()
                - _sock_path(session).stat().st_mtime) < _SPAWN_WAIT
    except OSError:
        return False


def _read_cmdline(pid: int):
    """读进程 cmdline：/proc/<pid>/cmdline → ps -o command= 回退。

    返回值三态语义：str（含空串）= pid 确有进程在跑且 cmdline 可读；
    None = 进程不存在或 cmdline 不可考（Windows 无 /proc 与 ps）。
    与 _pid_is_daemon/_pid_cmdline_is_bridge 的 False-on-unreadable
    判据不同——调用方需要区分"确认外来进程"与"读不出"。
    """
    try:
        p = Path(f'/proc/{pid}/cmdline')
        if p.is_file():
            return p.read_bytes().replace(b'\x00', b' ') \
                .decode('utf-8', 'replace')
    except Exception:
        pass
    try:
        r = subprocess.run(['ps', '-p', str(pid), '-o', 'command='],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return r.stdout or ''
    except Exception:
        pass
    return None


def _pid_is_daemon(pid: int) -> bool:
    """kill 前 pid 身份校验（POSIX）：cmdline 须含 'dp_cli.daemon'——
    meta 残留 pid 被 OS 复用时防误杀无关进程（bridge_manager
    ._pid_cmdline_is_bridge 同款）。/proc 读不到退化 ps；都读不到
    返回 False（宁可不杀只清标记）。Windows 不调用本函数——
    taskkill 分支保持原样。"""
    return 'dp_cli.daemon' in (_read_cmdline(pid) or '')


def _lock_pid_occupied(pid: int) -> bool:
    """stale 锁属主占用判定（_acquire_spawn_lock._excl 回收闸）。

    _pid_alive 单源有假死面——POSIX 跨 uid 进程 os.kill(pid,0) 抛
    PermissionError→False、Windows tasklist 失败兜底也 False，
    直接 unlink 等于偷活锁破坏互斥（双 spawn/双授权框坏状态）。
    真回收前用 cmdline 存在性复核：/proc 或 ps 任一能读到该 pid
    进程信息即"占用中"（含 pid 复用外来进程——记录 pid 确有进程
    在跑，无法证实属主已死）→ 保守不回收，由等待方超时兜底；
    两源皆死才确认属主已死可安全回收。"""
    if _pid_alive(pid):
        return True
    return _read_cmdline(pid) is not None


def _cleanup_stale(session: str) -> None:
    """stale 标记统一清理——{session}.sock + {session}.session(meta)
    + _READY_CACHE 就绪缓存项三件套一次清干净，防残留死标记让下条
    命令白等探活超时。所有 stale 判定路径（_daemon_ready/_status/
    daemon-start/spawn 前/daemon-stop）统一走本函数——不对称清理
    （只清 sock 漏 meta）会留孤儿 meta，误导 _version_probe/
    _connect_endpoint 读到死 pid/死端口。

    {session}.lock 刻意不清：flock 绑定打开文件描述，unlink 后新
    open 拿到的是新 inode——等待者会拿到"另一把锁"失去互斥。"""
    _READY_CACHE.pop(session, None)      # 探活缓存同步失效
    paths = [_meta_path(session), _sock_path(session)]
    mp = _meta_sock_path(session)        # 先读后删——meta.sock_path
    if mp:                               # 权威路径一并清（可与规则
        mp_p = Path(mp)                  # 路径不同处，防 TMPDIR 残留）
        if mp_p not in paths:
            paths.append(mp_p)
    for p in paths:
        try:
            p.unlink()
        except OSError:
            pass


_clean_stale = _cleanup_stale   # 兼容别名（旧调用点/外部引用）


_READY_CACHE = {}   # session → (ts, alive, 标记指纹)——探活 2s TTL


def _mark_fingerprint(session: str):
    """就绪缓存指纹：(meta, sock) mtime_ns 对——标记文件被外部
    改/删后指纹即变，命中判据含指纹防按名缓存脏读（session 文件
    被改写/删除后仍返回旧探活结果的缺陷）。缺失项记 None。"""
    def _mt(p):
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return None
    return (_mt(_meta_path(session)), _mt(_sock_path_eff(session)))


def _daemon_ready(session: str) -> bool:
    """daemon 就绪标记+探活——标记文件存在只是前提，connect_ex/
    meta pid 任一存活才算活；死标记自动清理（防每条命令先吃超时）。
    探活 2s TTL 缓存（双平台统一）：try_daemon 一次调用链探两次，
    TTL 复用省一倍 connect_ex；缓存命中须指纹一致——标记文件
    外部改/删即失效重查。POSIX spawn 窗口（sock 已 bind、
    meta 未落盘）由 _spawn_fresh 的 mtime 新鲜度判活防误清。"""
    if _HAS_UNIX_SOCK:
        # 存在性判 meta.sock_path 权威路径（两侧 TMPDIR 不同时
        # 规则路径缺失≠daemon 死，防误清活标记）
        if not _sock_path_eff(session).exists():
            if _meta_path(session).exists():
                # meta 只在 bind 后写——sock 不在=死标记孤儿 meta
                _cleanup_stale(session)
            return False
    elif not _meta_path(session).exists():
        return False
    now = time.monotonic()
    fp = _mark_fingerprint(session)
    hit = _READY_CACHE.get(session)
    if (isinstance(hit, tuple) and len(hit) >= 3
            and now - hit[0] < 2.0 and hit[2] == fp):
        return hit[1]
    if _HAS_UNIX_SOCK:
        ok_ = (_unix_alive(session) or _meta_pid_alive(session)
               or _spawn_fresh(session))
    else:
        ok_ = _tcp_alive(session) or _meta_pid_alive(session)
    if not ok_:
        _cleanup_stale(session)          # 死标记回收
    _READY_CACHE[session] = (now, ok_, fp)
    return ok_


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
    """-Sfoo/-S x/--session x/--session=x 四形态全认
    （对齐 serve._record_session 的 -S* 前缀判定）。"""
    for i, a in enumerate(argv):
        if a in ('--session', '-S') and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith('--session='):
            return a.split('=', 1)[1]
        if a.startswith('-S') and len(a) > 2:
            return a[2:]
    return 'default'


def _has_session_flag(argv: list) -> bool:
    """argv 是否已显式带 session 旗（与 _session_of 同判据）——
    默认会话注入的前置判定，'-Sfoo' 连写也算已带。"""
    return any(a in ('--session', '-S')
               or a.startswith('--session=')
               or (a.startswith('-S') and len(a) > 2)
               for a in (argv or ()))


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
    """一次 socket 往返 → dict 响应；异常返回 ('err', exc, sent)。

    sent=True 表示 sendall 已完整返回（请求送达 daemon）——此后
    recv 超时/断连重发会让命令重复执行（click/fill 非幂等），
    调用方只许对 sent=False（connect/sendall 阶段失败）重发。
    sendall 自身抛错仍记 False：未带换行的残行不会被 daemon
    当成命令解析，重发安全。"""
    sent = False
    try:
        if _HAS_UNIX_SOCK:
            # 规则路径先试，连不上读 meta.sock_path 兜底（client/
            # server TMPDIR 不同时规则路径会失联）；每个候选端点
            # 新建 socket——connect 失败后套接字状态未定义不可复用
            s = None
            last_e = None
            for ep in _unix_connect_eps(session):
                c = _socket.socket(_socket.AF_UNIX,
                                   _socket.SOCK_STREAM)
                c.settimeout(read_timeout)
                try:
                    c.connect(ep)
                    s = c
                    break
                except OSError as e:
                    last_e = e
                    c.close()
            if s is None:
                raise last_e if last_e is not None \
                    else OSError('无可用 unix 端点')
        else:
            s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            s.settimeout(read_timeout)
            s.setsockopt(_socket.IPPROTO_TCP,
                         _socket.TCP_NODELAY, 1)   # 单包协议免 Nagle
            s.connect(_connect_endpoint(session))
        s.sendall((json.dumps(payload, ensure_ascii=False) + '\n').encode())
        sent = True
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
        return ('err', e, sent)


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
        # LOCK_NB + 轮询到 deadline——裸 LOCK_EX 阻塞无上限，
        # 锁持有者卡死会把整个调用方拖死（netlog _append 走这条
        # 路径传 timeout=5）。对齐 _excl 分支的超时语义。
        deadline = time.monotonic() + timeout
        f = path.open('w')
        try:
            while True:
                try:
                    fcntl.flock(f.fileno(),
                                fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            f'spawn lock 超时: {path}')
                    time.sleep(0.05)
            yield
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            f.close()

    @contextlib.contextmanager
    def _excl():
        deadline = time.monotonic() + timeout
        fd = None
        eacces_misses = 0
        while fd is None:
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL
                             | os.O_WRONLY)
                break
            except FileExistsError:
                eacces_misses = 0
            except PermissionError:
                # Windows 竞争窗口：锁文件处于 sharing/pending-delete
                # 态时 CreateFile 报 EACCES 而非 EEXIST。文件存在 →
                # 与 FileExistsError 同走 stale 回收；不存在 → 多半
                # 是 open/stat 间的瞬时态（TOCTOU），短重试；连续多次
                # 仍不存在才是真权限错（目录不可写等），照抛。
                if path.exists():
                    eacces_misses = 0
                else:
                    eacces_misses += 1
                    if eacces_misses > 5:
                        raise
                    time.sleep(0.05)
                    continue
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
                # 属主判定加 cmdline 存在性复核——_pid_alive 假死
                # （跨 uid EPERM/tasklist 失败）或 pid 复用外来进程
                # 占用时无法证实属主已死，保守不回收防偷活锁
                alive = _lock_pid_occupied(lp)
            if not alive:
                try:
                    path.unlink()
                    continue
                except OSError:
                    pass
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
                # Windows 下竞争者瞬时打开会让 unlink 失败——残留的
                # 含"活 pid"锁文件会让等待方误判锁仍被持有（活 pid
                # 永不 stale），全员退化为每个 _excl 吃满 timeout。
                # 清空内容作为释放哨兵：读取方 lp<=0 且 mtime>1s 判
                # stale 回收，最坏 ~1s 即可恢复。
                try:
                    path.write_text('')
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
    _dir().mkdir(parents=True, exist_ok=True)
    try:
        with _acquire_spawn_lock(_dir() / f'{session}.lock'):
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
    r = (_send_recv(session, payload, rt) if _daemon_ready(session)
         else ('err', None, False))
    if isinstance(r, tuple):
        if len(r) > 2 and r[2]:
            _sent_timeout_exit(r[1])
        # sent=False：connect/sendall 阶段失败（ENOENT/ECONNREFUSED）
        # → 命令未送达 daemon，spawn 后重发一次是安全的
        if not _spawn_daemon(session):
            return None  # 起不来走本地，不阻塞
        r = _send_recv(session, payload, rt)
        if isinstance(r, tuple) and len(r) > 2 and r[2]:
            _sent_timeout_exit(r[1])
    if isinstance(r, tuple) or r is None:
        return None     # 未送达/daemon 仍不通 → 本地兜底
    resp = r.get('result', r) if isinstance(r, dict) else r
    # _write_out 走 UTF-8 字节写——非 UTF-8 locale（Windows GBK）下
    # sys.stdout.write 打非 ASCII 结果会 UnicodeEncodeError 静默丢响应
    _write_out(json.dumps(resp, ensure_ascii=False))
    try:
        return int(r.get('exit', 0)) if isinstance(r, dict) else 0
    except (TypeError, ValueError):
        return 0            # 畸形 exit 字段不崩客户端


def _sent_timeout_exit(exc):
    """sendall 送达后的超时/断连：只报错误绝不重发——重发=重复执行
    非幂等命令（click/fill/type），本地兜底同理禁用。"""
    error('daemon 已收命令但响应超时/断连（防重复执行不重发）',
          code='WAIT_TIMEOUT', detail=str(exc or ''))


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
                                           _FORBIDDEN, _GLOBAL_FLAGS,
                                           _record_session, _snap_state)
        from dp_cli.commands.batch import _invoke, _invalid
        argv, req_id, err = _parse_request(
            raw.decode('utf-8', errors='replace')) if raw else (None, None, '空行')
        _rec = None         # (session, argv, result, code, pre_state)
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
                if (not _has_session_flag(argv)
                        and _accepts_session(cmd_name)):
                    argv = argv + ['--session', session]
                # netlog 前置武装：首条导航命令的 Network 事件
                # 才能被捕获（后置会丢该命令全程流量）
                try:
                    from dp_cli.commands.serve import _maybe_arm_netlog
                    _maybe_arm_netlog()
                except Exception:
                    pass
                _rs = _record_session(argv, session)
                _pre = _snap_state(_rs)   # 执行前快照——未开录制 {} 零开销
                code, result = _invoke(argv)
                _rec = (_rs, argv, result, code, _pre)
        out = {'exit': code, 'result': result}
        if req_id is not None:
            out['id'] = req_id
        conn.sendall((json.dumps(out, ensure_ascii=False) + '\n').encode())
        if _rec is not None:
            # decide-train 录制旁路（响应已发出，不占应答延迟）——
            # 同 serve 渠道判据；异常吞掉不炸串行主循环
            try:
                from dp_cli.decide_train import _record_decide_row
                _record_decide_row(_rec[0], _rec[1], _rec[2],
                                   code=_rec[3], pre_state=_rec[4])
            except Exception as e:
                debug('decide-train 录制异常（已忽略）', e)
        if argv and argv[0] == 'daemon-stop':
            raise SystemExit(0)
    except SystemExit:
        raise
    except Exception:
        try:
            conn.sendall((json.dumps({
                'exit': 1,
                'result': {'status': 'error', 'code': 'ERROR',
                           'message': 'daemon 处理异常', 'detail': ''}},
                ensure_ascii=False) + '\n').encode('utf-8'))
        except OSError:
            pass
    finally:
        conn.close()


def _wake_acceptor(session: str) -> None:
    """idle 自退唤醒：向自身监听 socket 捅一发连接，让阻塞在 accept
    的主循环返回并看到 stop_ev——走 finally 清理 sock/meta 正常退出
    （替代 os._exit 硬退绕过清理的缺陷）。connect 失败重试几次，
    失败也只是沿用旧的"挂住"风险面，不比 os._exit 更糟。"""
    for _ in range(3):
        try:
            ep = _connect_endpoint(session)
            if _HAS_UNIX_SOCK:
                s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            else:
                s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect(ep)
            s.close()
            return
        except OSError:
            time.sleep(0.05)


def _idle_watcher(sock_path, meta_path, stop_ev, session):
    """headless 会话 1h 无活动自退；attached 会话按活动宽限豁免。

    user_connected/probe_dir 会话不再无条件豁免（只看连接态不看活动
    的残留缺陷）：浏览器端口还活着→宽限 _IDLE_TTL_ATTACHED；
    端口已死（浏览器退出/重启）→回落普通 _IDLE_TTL 自退。
    端口探测失败保守按活处理（宁可多留不误杀授权链路）。
    """
    while not stop_ev.wait(_WATCH_TICK):
        try:
            from dp_cli.session import load_session, _port_open
            sess = load_session(session) or {}
            idle = time.time() - _state['last_activity']
            ttl = _IDLE_TTL
            if sess.get('user_connected') or sess.get('probe_dir'):
                port = sess.get('port')
                if port:
                    try:
                        if _port_open('127.0.0.1', int(port)):
                            ttl = _IDLE_TTL_ATTACHED
                    except Exception:
                        ttl = _IDLE_TTL_ATTACHED   # 探测失败保守豁免
                else:
                    ttl = _IDLE_TTL_ATTACHED       # 无端口记录按活豁免
            if idle > ttl:
                debug(f'daemon 空闲 {idle:.0f}s > {ttl}s，自退')
                # 不 os._exit——绕过 run_daemon finally 会残留
                # sock/meta 死标记；置 stop_ev+自连唤醒 accept
                stop_ev.set()
                _wake_acceptor(session)
                return
        except Exception:
            pass


_state = {'last_activity': time.time(), 'active': 0}


def _busy_reply(conn) -> None:
    """并发闸满应答：立即回 BUSY 结构化错误并 close——连接
    不进入 _handle_conn，慢请求堆积不耗处理位。"""
    try:
        conn.sendall((json.dumps({
            'exit': 1,
            'result': {'status': 'error', 'code': 'BUSY',
                       'message': 'daemon 并发连接超限，稍后重试',
                       'detail': f'上限 {_MAX_CONN}'}},
            ensure_ascii=False) + '\n').encode('utf-8'))
    except OSError:
        pass
    try:
        conn.close()
    except OSError:
        pass


def _serve_conn(conn, session) -> None:
    """accept 后分派：闸满→BUSY 即关；否则处理中计数+1 进
    _handle_conn，try/finally 保证任何退出路径都释放计数
    （SystemExit 走 daemon-stop 正常语义向上抛）。"""
    if _state['active'] >= _MAX_CONN:
        _busy_reply(conn)
        return
    _state['active'] += 1
    try:
        _handle_conn(conn, session)
    finally:
        _state['active'] -= 1


def run_daemon(session: str):
    """daemon 主循环：bind socket → 每连接一个 JSONL 请求 → 应答。"""
    _dir().mkdir(parents=True, exist_ok=True)
    sock_path = _sock_path(session)
    meta_path = _meta_path(session)
    _cleanup_stale(session)      # 统一清理：sock+meta+缓存三件套
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
        # sock_path=server 实际 bind 的权威路径——client 规则算出
        # 的路径可能不一致（两侧 TMPDIR 不同/深 HOME 退短前缀），
        # client 连不上规则路径时以本字段兜底
        'sock_path': str(sock_path) if transport == 'unix' else None,
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
            if stop_ev.is_set():
                # idle 自退唤醒连接——不派给 _handle_conn，直接退
                try:
                    conn.close()
                except OSError:
                    pass
                break
            if not _HAS_UNIX_SOCK:
                conn.setsockopt(_socket.IPPROTO_TCP,
                               _socket.TCP_NODELAY, 1)
            _state['last_activity'] = time.time()
            try:
                _serve_conn(conn, session)
            except SystemExit:
                break
            except Exception:
                pass
    finally:
        srv.close()
        _cleanup_stale(session)  # 退出统一清理三件套
    return 0


def _status(session: str):
    meta_p = _meta_path(session)
    sock_p = _sock_path_eff(session)   # 权威路径（meta.sock_path 优先）
    marker = sock_p.exists() if _HAS_UNIX_SOCK \
        else meta_p.exists()
    if not marker:
        ok({'running': False, 'session': session})
        return
    meta = {}
    try:
        meta = json.loads(meta_p.read_text(encoding='utf-8'))
    except Exception:
        pass
    pid = meta.get('pid')
    if _HAS_UNIX_SOCK:
        # 对齐 _daemon_ready 探活三重——sock 可达/pid 活/spawn 窗口
        # （meta 未落盘+sock 新鲜）任一即活，防误判杀新 daemon
        alive = (_unix_alive(session)
                 or (_pid_alive(int(pid)) if pid else False)
                 or _spawn_fresh(session))
    else:
        # TCP 平台：端口可连或 pid 存活任一即活
        alive = _tcp_alive(session) or \
            (_pid_alive(int(pid)) if pid else False)
    if not alive:
        _cleanup_stale(session)      # 双平台死标记统一回收
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
        # 探活判据不凭标记文件存在——死 sock/meta 残留会误报
        # "已在运行"把用户卡死在只能手动 rm；_daemon_ready 内部
        # 已做探活+stale 回收（spawn 窗口同样豁免）
        if _daemon_ready(session):
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
        sock = _sock_path_eff(session)   # 权威路径（meta.sock_path 优先）
        meta_p = _meta_path(session)
        marker = sock.exists() if _HAS_UNIX_SOCK \
            else meta_p.exists()
        if not marker:
            ok({'stopped': False}, msg='daemon 未在运行')
            return
        _READY_CACHE.pop(session, None)
        r = _send_recv(session, {'argv': ['daemon-stop']}, 10)
        if isinstance(r, dict):
            _cleanup_stale(session)
            ok({'stopped': True}, msg='daemon 已停止')
            return
        try:
            pid = json.loads(meta_p.read_text()).get('pid')
            import sys as _sys
            if _sys.platform.startswith('win'):
                # Windows 无 POSIX SIGTERM 语义——taskkill /F 强制终止
                import subprocess as _sp
                _sp.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                        capture_output=True)
            elif pid and _pid_is_daemon(int(pid)):
                # 杀前身份校验——meta 残留 pid 被 OS 复用时防误杀
                os.kill(int(pid), 15)
            else:
                # pid 缺失/复用/cmdline 不可考——只清标记不杀
                _cleanup_stale(session)
                ok({'stopped': True, 'pid': pid},
                   msg='meta pid 非 dp_cli.daemon（疑复用），已清理标记')
                return
            _cleanup_stale(session)
            ok({'stopped': True, 'pid': pid}, msg='daemon 已 SIGTERM')
        except Exception:
            _cleanup_stale(session)
            ok({'stopped': True}, msg='清理了失效标记')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--session', default='default')
    args = ap.parse_args()
    sys.exit(run_daemon(args.session))
