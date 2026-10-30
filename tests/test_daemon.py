# -*- coding:utf-8 -*-
"""daemon socket 化测试——P1 故障路径定向覆盖。"""
import pytest


def test_daemon_stop_platform_branch(monkeypatch, tmp_path):
    """daemon stop win32 分派——真实调用路径，断言 taskkill 参数。"""
    import sys
    monkeypatch.setattr(sys, 'platform', 'win32')
    calls = []
    import subprocess
    monkeypatch.setattr(subprocess, 'run',
                        lambda *a, **k: calls.append(a[0]))
    monkeypatch.setattr('os.kill', lambda *a: (_ for _ in ()
                        .throw(AssertionError('POSIX kill 不应被调'))))
    import json, click
    from click.testing import CliRunner
    from dp_cli import daemon

    sock = tmp_path / 't.sock'; sock.write_text('')
    meta = tmp_path / 't.meta.json'
    meta.write_text(json.dumps({'pid': 4321, 'version': 'x'}))
    monkeypatch.setattr(daemon, '_sock_path', lambda s: sock)
    monkeypatch.setattr(daemon, '_meta_path', lambda s: meta)
    monkeypatch.setattr(daemon, '_send_recv', lambda *a, **k: None)
    monkeypatch.setattr(daemon, '_READY_CACHE', {})
    grp = click.Group()
    daemon.register(grp)
    r = CliRunner().invoke(grp, ['daemon-stop', '-S', 't'])
    assert r.exit_code == 0, r.output
    assert calls == [['taskkill', '/F', '/T', '/PID', '4321']], calls


# ── P1 修复定向测试 ─────────────────────────────────────────

def test_session_of_four_forms():
    """P1-5：-Sfoo/-S x/--session x/--session=x 四形态全认。"""
    from dp_cli import daemon
    assert daemon._session_of(['click', '-Sfoo']) == 'foo'
    assert daemon._session_of(['click', '-S', 'foo']) == 'foo'
    assert daemon._session_of(['click', '--session', 'x']) == 'x'
    assert daemon._session_of(['click', '--session=x']) == 'x'
    assert daemon._session_of(['click']) == 'default'
    assert daemon._session_of(['click', '--session']) == 'default'  # 缺值


def test_has_session_flag_forms():
    """P1-5：_has_session_flag 与 _session_of 同判据。"""
    from dp_cli import daemon
    for argv in (['-Sfoo'], ['-S', 'x'], ['--session', 'x'],
                 ['--session=x'], ['--session']):
        assert daemon._has_session_flag(argv), argv
    for argv in (['snapshot'], ['snapshot', '-v'], [], None,
                 ['--sessionx']):
        assert not daemon._has_session_flag(argv), argv


def _bind_tmp_marks(monkeypatch, daemon, tmp_path):
    sock = tmp_path / 't.sock'
    meta = tmp_path / 't.session'
    monkeypatch.setattr(daemon, '_sock_path', lambda s: sock)
    monkeypatch.setattr(daemon, '_meta_path', lambda s: meta)
    monkeypatch.setattr(daemon, '_READY_CACHE', {})
    return sock, meta


def test_daemon_ready_posix_stale_cleaned(monkeypatch, tmp_path):
    """P1-4：sock 存在但 connect 拒+meta pid 死 → False+死标记回收。"""
    import json
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    sock, meta = _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    sock.write_text('')
    meta.write_text(json.dumps({'pid': 2 ** 22}))
    monkeypatch.setattr(daemon, '_unix_alive', lambda s: False)
    monkeypatch.setattr(daemon, '_pid_alive', lambda p: False)
    assert daemon._daemon_ready('t') is False
    assert not sock.exists() and not meta.exists()


def test_daemon_ready_posix_alive_probe(monkeypatch, tmp_path):
    """P1-4：connect_ex 通 → True（对齐 TCP 分支探活语义）。"""
    import json
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    sock, meta = _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    sock.write_text('')
    meta.write_text(json.dumps({'pid': 2 ** 22}))
    monkeypatch.setattr(daemon, '_unix_alive', lambda s: True)
    monkeypatch.setattr(daemon, '_pid_alive', lambda p: False)
    assert daemon._daemon_ready('t') is True


def test_daemon_ready_posix_spawn_window(monkeypatch, tmp_path):
    """P1-4：spawn 窗口——sock 刚 bind、meta 未落盘、connect 暂拒 →
    mtime 新鲜判活，不误清新 daemon 标记。"""
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    sock, _ = _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    sock.write_text('')                      # meta 不存在=spawn 窗口
    monkeypatch.setattr(daemon, '_unix_alive', lambda s: False)
    monkeypatch.setattr(daemon, '_pid_alive', lambda p: False)
    assert daemon._daemon_ready('t') is True
    assert sock.exists()                     # 未被误清


# ── sun_path 兜底：深 HOME 退 TMPDIR 短前缀 ────────────────────

def _deep_dir():
    """确定性深目录（纯路径计算不建文件）：>100B 必超 sun_path 上限。"""
    import os
    from pathlib import Path
    return Path(os.path.abspath(os.sep)) / ('d' * 120)


def test_sock_path_sun_path_fallback_deep_home(monkeypatch):
    """sun_path 兜底：sock 路径超 AF_UNIX 上限（>100B）退
    tempfile.gettempdir()/dpx-<sha1>.sock——深 HOME（长用户名/
    网络 home）下 bind/connect 不再抛 'AF_UNIX path too long'。"""
    import os
    import tempfile
    from pathlib import Path
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    monkeypatch.setattr(daemon, '_dir', _deep_dir)
    p = daemon._sock_path('s')
    assert p.parent == Path(tempfile.gettempdir())
    assert p.name.startswith('dpx-') and p.name.endswith('.sock')
    assert len(os.fsencode(str(p))) <= daemon._SUN_PATH_LIMIT
    # 同 session 可重建——client connect/server bind/stale 清理
    # 全走 _sock_path 天然同决策
    assert daemon._sock_path('s') == p
    # 不同 session 不互踩
    assert daemon._sock_path('s2') != p
    # _connect_endpoint 与 _sock_path 同决策（meta 不受限留原路径）
    assert daemon._connect_endpoint('s') == str(p)
    assert daemon._meta_path('s') == _deep_dir() / 's.session'


def test_sock_path_short_home_unchanged(monkeypatch):
    """短路径行为不变：sock 在 _dir() 内、文件名 {session}.sock。"""
    import os
    from pathlib import Path
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    d = Path(os.path.abspath(os.sep)) / 'd'    # len('/d/s.sock')=9B
    monkeypatch.setattr(daemon, '_dir', lambda: d)
    assert daemon._sock_path('s') == d / 's.sock'


def test_sock_path_sun_path_boundary(monkeypatch):
    """边界值：=100B 不兜底（Linux 108/macOS 104 余量内），
    101B 起退短前缀。"""
    import os
    from pathlib import Path
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    root = Path(os.path.abspath(os.sep))
    # '/<name>/s.sock' 凑整：总长 = name 长 + len('//s.sock')=+8
    d100 = root / ('e' * 92)
    assert len(os.fsencode(str(d100 / 's.sock'))) == 100
    monkeypatch.setattr(daemon, '_dir', lambda: d100)
    assert daemon._sock_path('s') == d100 / 's.sock'
    d101 = root / ('e' * 93)
    assert len(os.fsencode(str(d101 / 's.sock'))) == 101
    monkeypatch.setattr(daemon, '_dir', lambda: d101)
    assert daemon._sock_path('s') != d101 / 's.sock'


def test_sock_path_tcp_platform_no_fallback(monkeypatch):
    """Windows（无 AF_UNIX）不做 sun_path 兜底——sock 路径形态不变
    （平台分支只按 _HAS_UNIX_SOCK 判定）。"""
    from dp_cli import daemon
    monkeypatch.setattr(daemon, '_HAS_UNIX_SOCK', False)
    monkeypatch.setattr(daemon, '_dir', _deep_dir)
    assert daemon._sock_path('s') == _deep_dir() / 's.sock'


# ── 跨平台降级：_HAS_UNIX_SOCK=False 强制 TCP 分支 ─────────────
# （对齐 test_commands.py 的 _HAS_UNIX_SOCK mock 先例——开发机
# 是 POSIX，Windows 路径只能靠 mock 平台常量跑分支验证）

def _tcp_sock_factory(calls, connect_ex_ret=0, response=b''):
    """伪造 socket.socket——记录 family/端点/调用，connect_ex 返回
    值可编程（断言 AF_INET vs AF_UNIX 行为分叉用）。"""
    def _factory(family, sock_type, *a, **k):
        calls.append(('socket', family, sock_type))

        class _S:
            _resp = response

            def settimeout(self, t):
                calls.append(('settimeout', t))

            def setsockopt(self, *args):
                calls.append(('setsockopt',) + args)

            def connect_ex(self, ep):
                calls.append(('connect_ex', ep))
                return connect_ex_ret

            def connect(self, ep):
                calls.append(('connect', ep))

            def sendall(self, b):
                calls.append(('sendall', len(b)))

            def recv(self, n):
                d, self._resp = self._resp, b''
                return d

            def close(self):
                pass
        return _S()
    return _factory


def test_daemon_tcp_endpoint_and_send_recv_family(monkeypatch, tmp_path):
    """TCP 降级：_connect_endpoint 从 meta port 解析 ('127.0.0.1',
    port) 元组；_send_recv 建 AF_INET socket+TCP_NODELAY 并 connect
    到该端点——不触 AF_UNIX .sock 路径。"""
    import json
    from dp_cli import daemon
    _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    monkeypatch.setattr(daemon, '_HAS_UNIX_SOCK', False)
    (tmp_path / 't.session').write_text(
        json.dumps({'pid': 1234, 'port': 54321}))
    assert daemon._connect_endpoint('t') == ('127.0.0.1', 54321)

    calls = []
    monkeypatch.setattr(
        daemon._socket, 'socket',
        _tcp_sock_factory(
            calls, response=b'{"exit":0,"result":{"ok":1}}\n'))
    r = daemon._send_recv('t', {'argv': ['snapshot']}, 5)
    assert r['exit'] == 0 and r['result']['ok'] == 1
    assert ('socket', daemon._socket.AF_INET,
            daemon._socket.SOCK_STREAM) in calls
    assert ('connect', ('127.0.0.1', 54321)) in calls
    # TCP_NODELAY 仅在 TCP 分支设置（AF_UNIX 无此 setsockopt）
    assert any(c[0] == 'setsockopt' for c in calls)
    assert not any(c[0] == 'socket'
                   and c[1] == getattr(daemon._socket, 'AF_UNIX', -1)
                   for c in calls)


def test_daemon_ready_tcp_probe_alive_via_connect_ex(
        monkeypatch, tmp_path):
    """TCP 探活分叉（活）：meta port + connect_ex==0 → True——
    _daemon_ready 走 meta+connect_ex 分支，AF_UNIX 专属的
    _unix_alive/_spawn_fresh 不应触达。"""
    import json
    from dp_cli import daemon
    _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    monkeypatch.setattr(daemon, '_HAS_UNIX_SOCK', False)
    (tmp_path / 't.session').write_text(
        json.dumps({'pid': -1, 'port': 54321}))
    calls = []
    monkeypatch.setattr(daemon._socket, 'socket',
                        _tcp_sock_factory(calls, connect_ex_ret=0))
    monkeypatch.setattr(daemon, '_unix_alive',
                        lambda s: (_ for _ in ()).throw(AssertionError(
                            'TCP 分支不调 _unix_alive')))
    monkeypatch.setattr(daemon, '_spawn_fresh',
                        lambda s: (_ for _ in ()).throw(AssertionError(
                            'TCP 分支不调 _spawn_fresh')))
    assert daemon._daemon_ready('t') is True
    assert ('connect_ex', ('127.0.0.1', 54321)) in calls


def test_daemon_ready_tcp_probe_refused_cleans_meta(
        monkeypatch, tmp_path):
    """TCP 探活分叉（死）：connect_ex 拒连+meta pid 死 → False
    且 meta 回收——死标记不留，下条命令不白等 TCP 超时。"""
    import json
    from dp_cli import daemon
    _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    monkeypatch.setattr(daemon, '_HAS_UNIX_SOCK', False)
    meta = tmp_path / 't.session'
    meta.write_text(json.dumps({'pid': -1, 'port': 54321}))
    calls = []
    monkeypatch.setattr(
        daemon._socket, 'socket',
        _tcp_sock_factory(calls, connect_ex_ret=111))   # ECONNREFUSED
    monkeypatch.setattr(daemon, '_pid_alive', lambda p: False)
    assert daemon._daemon_ready('t') is False
    assert ('connect_ex', ('127.0.0.1', 54321)) in calls
    assert not meta.exists()                 # 死标记已清


class _FakeSock:
    """_send_recv 故障注入用 socket 桩。"""
    def __init__(self, fail_at):
        self.fail_at = fail_at
    def settimeout(self, t):
        pass
    def setsockopt(self, *a):
        pass
    def connect(self, ep):
        if self.fail_at == 'connect':
            raise OSError('refused')
    def sendall(self, b):
        if self.fail_at == 'sendall':
            raise OSError('reset')
    def recv(self, n):
        if self.fail_at == 'recv':
            raise OSError('timeout')
        return b''
    def close(self):
        pass


def test_send_recv_connect_fail_not_sent(monkeypatch):
    """P1-6：connect 失败 sent=False——调用方可安全重发。"""
    from dp_cli import daemon
    monkeypatch.setattr(daemon, '_connect_endpoint', lambda s: 'x')
    monkeypatch.setattr(daemon._socket, 'socket',
                        lambda *a, **k: _FakeSock('connect'))
    r = daemon._send_recv('s', {'argv': ['x']}, 1)
    assert r[0] == 'err' and r[2] is False


def test_send_recv_recv_fail_after_send_sent(monkeypatch):
    """P1-6：sendall 成功后 recv 超时 sent=True——禁止重发。"""
    from dp_cli import daemon
    monkeypatch.setattr(daemon, '_connect_endpoint', lambda s: 'x')
    monkeypatch.setattr(daemon._socket, 'socket',
                        lambda *a, **k: _FakeSock('recv'))
    r = daemon._send_recv('s', {'argv': ['x']}, 1)
    assert r[0] == 'err' and r[2] is True


def test_send_recv_sendall_fail_not_sent(monkeypatch):
    """P1-6：sendall 自身抛错 sent=False——残行无换行不会被
    daemon 解析成命令，重发安全。"""
    from dp_cli import daemon
    monkeypatch.setattr(daemon, '_connect_endpoint', lambda s: 'x')
    monkeypatch.setattr(daemon._socket, 'socket',
                        lambda *a, **k: _FakeSock('sendall'))
    r = daemon._send_recv('s', {'argv': ['x']}, 1)
    assert r[0] == 'err' and r[2] is False


def test_try_daemon_no_resend_after_sent(monkeypatch, capsys):
    """P1-6：命令已送达后超时→报超时错误退出，不 respawn 不重发
    不落本地（幂等铁律）。"""
    from dp_cli import daemon
    monkeypatch.setattr(daemon, '_daemon_ready', lambda s: True)
    monkeypatch.setattr(daemon, '_send_recv',
                        lambda *a, **k: ('err', OSError('t'), True))
    spawns = []
    monkeypatch.setattr(daemon, '_spawn_daemon',
                        lambda s: spawns.append(s) or True)
    with pytest.raises(SystemExit) as ei:
        daemon.try_daemon(['snapshot'])
    assert ei.value.code == 5                # WAIT_TIMEOUT → exit 5
    assert spawns == []                      # 已送达绝不重发
    assert '不重发' in capsys.readouterr().out


def test_try_daemon_connect_fail_respawns(monkeypatch, capsys):
    """P1-6：connect 失败（未送达）→ spawn+重发一次是安全的。"""
    import json
    from dp_cli import daemon
    monkeypatch.setattr(daemon, '_daemon_ready', lambda s: True)
    seq = iter([('err', OSError('refused'), False),
                {'exit': 0, 'result': {'status': 'ok'}}])
    monkeypatch.setattr(daemon, '_send_recv', lambda *a, **k: next(seq))
    monkeypatch.setattr(daemon, '_spawn_daemon', lambda s: True)
    assert daemon.try_daemon(['snapshot']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'ok'


def test_try_daemon_write_out_unicode_and_bad_exit(monkeypatch, capsys):
    """P1-7：响应走 _write_out（非 ASCII 不炸）；畸形 exit 兜底 0。"""
    import json
    from dp_cli import daemon
    monkeypatch.setattr(daemon, '_daemon_ready', lambda s: True)
    monkeypatch.setattr(daemon, '_send_recv', lambda *a, **k: {
        'exit': 'x', 'result': {'status': 'ok', 'data': '中文⚡'}})
    assert daemon.try_daemon(['snapshot']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'ok' and out['data'] == '中文⚡'


def test_idle_watcher_clean_exit_no_os_exit(monkeypatch):
    """P1-3：空闲自退走 stop_ev.set()+_wake_acceptor 正常退出路径，
    不 os._exit 绕过 finally。"""
    import os, time
    from dp_cli import daemon

    class _Ev:
        def __init__(self):
            self.flag = False
        def wait(self, t):
            return self.flag
        def set(self):
            self.flag = True
        def is_set(self):
            return self.flag

    ev = _Ev()
    monkeypatch.setitem(daemon._state, 'last_activity',
                        time.time() - daemon._IDLE_TTL - 1)
    monkeypatch.setattr('dp_cli.session.load_session', lambda s: {})
    wakes = []
    monkeypatch.setattr(daemon, '_wake_acceptor', lambda s: wakes.append(s))
    monkeypatch.setattr(os, '_exit',
                        lambda c: (_ for _ in ()).throw(
                            AssertionError('os._exit 不应被调')))
    daemon._idle_watcher(None, None, ev, 's')
    assert ev.flag is True and wakes == ['s']


def test_run_daemon_idle_self_exit_e2e(monkeypatch, tmp_path, sock_dir):
    """P1-3 e2e：idle 超时→wake 连接→accept 返回看到 stop_ev→
    finally 清理 sock/meta——真 socket 全链路。

    sock_dir：深 tmp_path 下 AF_UNIX bind 抛 'path too long'（macOS
    104B 上限）——此前 daemon 线程静默死亡后断言恰好全过（误绿），
    短路径 fixture 让真实 bind 链路在 CI 上真正跑到。"""
    import threading, time
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    monkeypatch.setattr(daemon, '_IDLE_TTL', 0)       # 立即超时
    monkeypatch.setattr(daemon, '_WATCH_TICK', 0.02)
    monkeypatch.setattr(daemon, '_sock_path',
                        lambda s: sock_dir / f'{s}.sock')
    monkeypatch.setattr(daemon, '_meta_path',
                        lambda s: tmp_path / f'{s}.session')
    monkeypatch.setattr('dp_cli.session.load_session', lambda s: {})
    t = threading.Thread(target=daemon.run_daemon, args=('t',),
                         daemon=True)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive()                            # 正常退出非挂死
    assert not (sock_dir / 't.sock').exists()          # finally 清理
    assert not (tmp_path / 't.session').exists()


def test_pid_is_daemon_proc_cmdline(monkeypatch):
    """P1-8：/proc cmdline 判据——含 dp_cli.daemon 才认。"""
    from dp_cli import daemon

    class _P:
        def __init__(self, name):
            pass
        def is_file(self):
            return True
        def read_bytes(self):
            return b'python\x00-m\x00dp_cli.daemon\x00--session\x00x'

    monkeypatch.setattr(daemon, 'Path', _P)
    assert daemon._pid_is_daemon(1) is True

    class _P2(_P):
        def read_bytes(self):
            return b'python\x00-m\x00other.mod'

    monkeypatch.setattr(daemon, 'Path', _P2)
    assert daemon._pid_is_daemon(1) is False


def test_pid_is_daemon_ps_fallback(monkeypatch):
    """P1-8：无 /proc（macOS）退化 ps -o command= 判据。"""
    from dp_cli import daemon

    class _P:
        def __init__(self, name):
            pass
        def is_file(self):
            return False

    monkeypatch.setattr(daemon, 'Path', _P)
    monkeypatch.setattr(daemon.subprocess, 'run', lambda *a, **k:
                        type('R', (), {'returncode': 0,
                                       'stdout': 'python -m '
                                       'dp_cli.daemon --session x'})())
    assert daemon._pid_is_daemon(7) is True
    monkeypatch.setattr(daemon.subprocess, 'run', lambda *a, **k:
                        type('R', (), {'returncode': 0,
                                       'stdout': '/usr/bin/sshd'})())
    assert daemon._pid_is_daemon(7) is False


def test_daemon_stop_posix_kill_requires_identity(monkeypatch, tmp_path):
    """P1-8：meta pid 身份校验——非 dp_cli.daemon 只清标记不 os.kill
    （防 PID 复用误杀）；身份确认才 SIGTERM。"""
    import os, sys, json, click
    from click.testing import CliRunner
    from dp_cli import daemon
    monkeypatch.setattr(sys, 'platform', 'linux')
    sock = tmp_path / 't.sock'
    sock.write_text('')
    meta = tmp_path / 't.session'
    monkeypatch.setattr(daemon, '_sock_path', lambda s: sock)
    monkeypatch.setattr(daemon, '_meta_path', lambda s: meta)
    monkeypatch.setattr(daemon, '_send_recv',
                        lambda *a, **k: ('err', None, False))
    monkeypatch.setattr(daemon, '_READY_CACHE', {})
    grp = click.Group()
    daemon.register(grp)

    # 身份不确认 → 不 kill，只清标记
    meta.write_text(json.dumps({'pid': 4321}))
    monkeypatch.setattr(daemon, '_pid_is_daemon', lambda p: False)
    monkeypatch.setattr(os, 'kill',
                        lambda *a: (_ for _ in ()).throw(
                            AssertionError('身份未确认不应 kill')))
    r = CliRunner().invoke(grp, ['daemon-stop', '-S', 't'])
    assert r.exit_code == 0, r.output
    assert '复用' in r.output

    # 身份确认 → SIGTERM
    sock.write_text('')
    meta.write_text(json.dumps({'pid': 4321}))
    killed = []
    monkeypatch.setattr(daemon, '_pid_is_daemon', lambda p: True)
    monkeypatch.setattr(os, 'kill', lambda p, sig: killed.append((p, sig)))
    r = CliRunner().invoke(grp, ['daemon-stop', '-S', 't'])
    assert r.exit_code == 0, r.output
    assert killed == [(4321, 15)]


# ── 断点3：daemon 渠道 decide-train 录制 ─────────────────────

class _FakeConn:
    """_handle_conn 用 socket 桩：一次性吐请求字节，收集响应。"""
    def __init__(self, raw):
        self._raw, self.sent = raw, b''
        self.closed = False
    def settimeout(self, t):
        pass
    def recv(self, n):
        d, self._raw = self._raw, b''
        return d
    def sendall(self, b):
        self.sent += b
    def close(self):
        self.closed = True


def _stub_record_env(monkeypatch, rec):
    """daemon 录制测试公共 mock：env + record 收集 + snap/refs。"""
    import dp_cli.commands.batch as batch_mod
    import dp_cli.commands.serve as sv
    monkeypatch.setenv('DPX_RECORD_DECIDE', '1')
    monkeypatch.setattr('dp_cli.decide_train.record',
                        lambda *a, **k: rec.append((a, k)))
    monkeypatch.setattr('dp_cli.session.load_refs',
                        lambda s: {'1': {'role': 'button',
                                         'name': '登录'}})
    monkeypatch.setattr(sv, '_snap_state',
                        lambda s: {'url': 'https://x/pre'})
    return batch_mod, sv


def test_handle_conn_records_decide_row(monkeypatch):
    """断点3：daemon _handle_conn 执行成功走公共录制——decide 行带
    engine_ref/goal/pre_state；失败路径（code!=0）不录。"""
    import json
    from dp_cli import daemon
    rec = []
    batch_mod, _ = _stub_record_env(monkeypatch, rec)
    results = iter([(0, {'status': 'ok', 'data': {'ref': 1}}),
                    (1, {'status': 'error', 'code': 'ERROR',
                         'message': 'x'})])
    monkeypatch.setattr(batch_mod, '_invoke', lambda a: next(results))

    c1 = _FakeConn('{"argv": ["decide", "点登录"]}\n'
                     .encode('utf-8'))
    daemon._handle_conn(c1, 't')
    resp = json.loads(c1.sent.split(b'\n')[0])
    assert resp['exit'] == 0                       # 响应照常送达
    c2 = _FakeConn(b'{"argv": ["click", "@e1"]}\n')
    daemon._handle_conn(c2, 't')                   # code=1 → 不录
    assert len(rec) == 1
    a, k = rec[0]
    assert a[0] == 't' and a[1][0] == 'decide'
    assert k['goal'] == '点登录' and k['engine_ref'] == 1
    assert k['unsure'] is False
    assert k['pre_state'] == {'url': 'https://x/pre'}
    assert k['post_state'] == {'url': 'https://x/pre'}


def test_handle_conn_record_fault_tolerant(monkeypatch):
    """断点3：record 抛异常吞掉走 debug——响应照常送达不炸
    串行主循环；录制关闭（无 env）时连 load_refs 都不发生。"""
    import json
    from dp_cli import daemon
    import dp_cli.commands.batch as batch_mod
    import dp_cli.commands.serve as sv

    rec = []
    batch_mod, _ = _stub_record_env(monkeypatch, rec)
    monkeypatch.setattr('dp_cli.decide_train.record',
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError('boom')))
    monkeypatch.setattr(batch_mod, '_invoke',
                        lambda a: (0, {'status': 'ok'}))
    c = _FakeConn(b'{"argv": ["click", "@e1"]}\n')
    daemon._handle_conn(c, 't')
    assert json.loads(c.sent.split(b'\n')[0])['exit'] == 0
    assert rec == []                               # raise 的路径未入 rec

    # 录制关闭：load_refs/record 均不触达（热路径零额外 IO）
    monkeypatch.delenv('DPX_RECORD_DECIDE')
    refs_calls = []
    monkeypatch.setattr('dp_cli.session.load_refs',
                        lambda s: refs_calls.append(s) or {})
    monkeypatch.setattr('dp_cli.decide_train.record',
                        lambda *a, **k: rec.append((a, k)))
    c = _FakeConn(b'{"argv": ["click", "@e1"]}\n')
    daemon._handle_conn(c, 't')
    assert json.loads(c.sent.split(b'\n')[0])['exit'] == 0
    assert refs_calls == [] and rec == []


# ── P2：stale-marker / 状态残留族 ──────────────────────────

def test_cleanup_stale_removes_sock_meta_cache(monkeypatch, tmp_path):
    """P2-1：_cleanup_stale 三件套——sock+meta 双文件 unlink +
    _READY_CACHE 就绪缓存项同步失效。"""
    import json
    from dp_cli import daemon
    sock, meta = _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    sock.write_text('')
    meta.write_text(json.dumps({'pid': 1}))
    daemon._READY_CACHE['t'] = (0, True, None)
    daemon._cleanup_stale('t')
    assert not sock.exists() and not meta.exists()
    assert 't' not in daemon._READY_CACHE


def test_daemon_ready_orphan_meta_cleaned(monkeypatch, tmp_path):
    """P2-1：POSIX sock 缺失但 meta 残留——孤儿 meta 顺手回收
    （meta 只在 bind 后落盘，sock 不在=死标记）。"""
    import json
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    sock, meta = _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    meta.write_text(json.dumps({'pid': 12345}))
    assert daemon._daemon_ready('t') is False
    assert not meta.exists()                    # 孤儿 meta 被清


def test_daemon_ready_cache_fingerprint_invalidation(
        monkeypatch, tmp_path):
    """P2-2：_READY_CACHE 脏读防护——标记文件 mtime 指纹入缓存，
    外部改/删 session 标记文件即令缓存失效重查。"""
    import json, os
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    sock, meta = _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    sock.write_text('')
    meta.write_text(json.dumps({'pid': 1}))
    calls = []
    monkeypatch.setattr(daemon, '_unix_alive',
                        lambda s: calls.append(1) or True)
    assert daemon._daemon_ready('t') is True
    assert daemon._daemon_ready('t') is True
    assert len(calls) == 1                      # TTL 内指纹一致 → 命中
    # 外部改写 meta → mtime_ns 变 → 指纹不符 → 重查
    st = meta.stat()
    os.utime(meta, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert daemon._daemon_ready('t') is True
    assert len(calls) == 2
    # 外部删除 meta → 指纹 (None, sock_ns) ≠ 缓存 → 重查
    meta.unlink()
    assert daemon._daemon_ready('t') is True
    assert len(calls) == 3


def test_status_posix_dead_marker_cleanup(monkeypatch, tmp_path, capsys):
    """P2-1：_status POSIX 分支死标记统一回收（原只 TCP 分支清）；
    spawn 窗口（meta 无+sock 新）由 _spawn_fresh 豁免不误清。"""
    import json
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    sock, meta = _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    sock.write_text('')
    meta.write_text(json.dumps({'pid': 99999}))
    monkeypatch.setattr(daemon, '_unix_alive', lambda s: False)
    monkeypatch.setattr(daemon, '_pid_alive', lambda p: False)
    daemon._status('t')
    assert not sock.exists() and not meta.exists()
    out = json.loads(capsys.readouterr().out)
    assert out['data']['running'] is False
    # spawn 窗口豁免：meta 无+sock 新鲜 → 不判死不清 sock
    sock.write_text('')
    daemon._status('t')
    out2 = json.loads(capsys.readouterr().out)
    assert out2['data']['running'] is True
    assert sock.exists()


def test_daemon_start_stale_marker_respawns(monkeypatch, tmp_path):
    """P2-1：daemon-start 不再凭 sock.exists() 判活——stale 标记
    走 _daemon_ready 探活+回收后 spawn；活 daemon 才报已在运行。"""
    import click
    from click.testing import CliRunner
    from dp_cli import daemon
    _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    grp = click.Group()
    daemon.register(grp)
    spawns = []
    monkeypatch.setattr(daemon, '_spawn_daemon',
                        lambda s: spawns.append(s) or True)
    # 死标记 → _daemon_ready False → 走 spawn 不误报已在运行
    monkeypatch.setattr(daemon, '_daemon_ready', lambda s: False)
    r = CliRunner().invoke(grp, ['daemon-start', '-S', 't'])
    assert r.exit_code == 0, r.output
    assert spawns == ['t'] and '已启动' in r.output
    # 活 daemon → 已在运行，不重复 spawn
    spawns.clear()
    monkeypatch.setattr(daemon, '_daemon_ready', lambda s: True)
    r = CliRunner().invoke(grp, ['daemon-start', '-S', 't'])
    assert '已在运行' in r.output and spawns == []


def _force_excl_lock(monkeypatch):
    """强制 _acquire_spawn_lock 走 Windows O_EXCL 降级分支。"""
    import builtins
    real_import = builtins.__import__

    def no_fcntl(name, *a, **k):
        if name == 'fcntl':
            raise ImportError('No module named fcntl')
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, '__import__', no_fcntl)


def test_spawn_lock_excl_owner_cmdline_rescue(monkeypatch, tmp_path):
    """P2-4：_excl stale 属主双源核验——_pid_alive 假死（跨 uid
    EPERM/tasklist 失败）但 cmdline 能读到该 pid → 不确认不回收，
    等满超时也不偷活锁。"""
    import os, time
    from dp_cli import daemon
    _force_excl_lock(monkeypatch)
    lp = tmp_path / 's.lock'
    lp.write_text('4321')
    os.utime(str(lp), (time.time() - 10, time.time() - 10))
    monkeypatch.setattr(daemon, '_pid_alive', lambda p: False)
    monkeypatch.setattr(daemon, '_read_cmdline',
                        lambda p: 'python dpx snapshot')   # pid 确有进程
    with pytest.raises(TimeoutError):
        with daemon._acquire_spawn_lock(lp, timeout=0.3):
            pass
    assert lp.exists()                          # 未被误回收
    assert lp.read_text() == '4321'


def test_spawn_lock_excl_foreign_pid_no_steal(monkeypatch, tmp_path):
    """P2-4：记录 pid 存活但 cmdline 非属主形态（pid 复用外来
    进程）→ 保守不偷锁——误回收破坏互斥的代价大于等待超时。"""
    import os, time
    from dp_cli import daemon
    _force_excl_lock(monkeypatch)
    lp = tmp_path / 's.lock'
    lp.write_text('4321')
    os.utime(str(lp), (time.time() - 10, time.time() - 10))
    monkeypatch.setattr(daemon, '_pid_alive', lambda p: True)
    monkeypatch.setattr(daemon, '_read_cmdline',
                        lambda p: '/usr/sbin/sshd -D')     # 非 daemon 属主
    with pytest.raises(TimeoutError):
        with daemon._acquire_spawn_lock(lp, timeout=0.3):
            pass
    assert lp.exists()


def test_spawn_lock_excl_confirmed_dead_reclaims(monkeypatch, tmp_path):
    """P2-4：pid 无+ cmdline 读不出（两源皆死）→ 确认属主已死，
    stale 锁回收正常获取。"""
    import os, time
    from dp_cli import daemon
    _force_excl_lock(monkeypatch)
    lp = tmp_path / 's.lock'
    lp.write_text('4321')
    os.utime(str(lp), (time.time() - 10, time.time() - 10))
    monkeypatch.setattr(daemon, '_pid_alive', lambda p: False)
    monkeypatch.setattr(daemon, '_read_cmdline', lambda p: None)
    with daemon._acquire_spawn_lock(lp, timeout=1):
        pass
    assert not lp.exists()                      # 获取+释放后无残留


# ── P2：DoS——daemon 并发连接闸 ─────────────────────────────

def test_serve_conn_busy_gate_full(monkeypatch):
    """P2-DoS：处理中计数达 _MAX_CONN → accept 后立即 BUSY 结构
    化应答+close，不进 _handle_conn（慢请求不耗处理位）。"""
    import json
    from dp_cli import daemon
    handled = []
    monkeypatch.setattr(daemon, '_handle_conn',
                        lambda c, s: handled.append(c))
    monkeypatch.setitem(daemon._state, 'active', daemon._MAX_CONN)
    c = _FakeConn(b'{"argv": ["snapshot"]}\n')
    daemon._serve_conn(c, 't')
    resp = json.loads(c.sent.split(b'\n')[0])
    assert resp['result']['status'] == 'error'
    assert resp['result']['code'] == 'BUSY'
    assert 'message' in resp['result'] and 'detail' in resp['result']
    assert c.closed is True                      # 连接立即关闭
    assert handled == []                         # 未进入处理
    assert daemon._state['active'] == daemon._MAX_CONN  # 计数未动


def test_serve_conn_counts_inflight_and_releases(monkeypatch):
    """P2-DoS：闸未满 → active inc/dec 成对；_handle_conn 抛
    异常也在 finally 释放计数（不泄漏闸位）。"""
    from dp_cli import daemon
    seen = []

    def fake_handle(conn, session):
        seen.append(daemon._state['active'])
        raise RuntimeError('boom')

    monkeypatch.setattr(daemon, '_handle_conn', fake_handle)
    monkeypatch.setitem(daemon._state, 'active', 0)
    c = _FakeConn(b'x\n')
    with pytest.raises(RuntimeError):
        daemon._serve_conn(c, 't')
    assert seen == [1]                           # 处理中计数=1
    assert daemon._state['active'] == 0          # finally 已释放


def test_serve_conn_systemexit_releases_counter(monkeypatch):
    """P2-DoS：daemon-stop 的 SystemExit 穿透 _serve_conn——
    finally 释放计数后向上抛（accept 循环 break 语义不变）。"""
    from dp_cli import daemon
    monkeypatch.setattr(daemon, '_handle_conn',
                        lambda c, s: (_ for _ in ()).throw(
                            SystemExit(0)))
    monkeypatch.setitem(daemon._state, 'active', 0)
    c = _FakeConn(b'x\n')
    with pytest.raises(SystemExit):
        daemon._serve_conn(c, 't')
    assert daemon._state['active'] == 0


class _TickOnceEv:
    """wait() 第一次返回 False（执行一轮检查）、之后 True（退出循环）。"""

    def __init__(self):
        self.flag = False
        self.calls = 0

    def wait(self, t):
        self.calls += 1
        return self.calls > 1 or self.flag

    def set(self):
        self.flag = True

    def is_set(self):
        return self.flag


def test_idle_watcher_attached_live_port_extended_ttl(monkeypatch):
    """P2：attached(user_connected) 会话浏览器端口活→宽限 TTL 豁免，
    idle 超普通 TTL 也不自退。"""
    import time
    from dp_cli import daemon
    ev = _TickOnceEv()
    monkeypatch.setitem(daemon._state, 'last_activity',
                        time.time() - daemon._IDLE_TTL - 10)
    monkeypatch.setattr('dp_cli.session.load_session',
                        lambda s: {'user_connected': True, 'port': 9333})
    monkeypatch.setattr('dp_cli.session._port_open',
                        lambda ip, p, timeout=0.3: True)
    daemon._idle_watcher(None, None, ev, 's')
    assert ev.flag is False        # 宽限 TTL 内——不自退


def test_idle_watcher_attached_dead_port_normal_ttl(monkeypatch):
    """P2：attached 会话浏览器端口已死→豁免无依据，回落普通 TTL 自退。"""
    import time
    from dp_cli import daemon
    ev = _TickOnceEv()
    monkeypatch.setitem(daemon._state, 'last_activity',
                        time.time() - daemon._IDLE_TTL - 10)
    monkeypatch.setattr('dp_cli.session.load_session',
                        lambda s: {'user_connected': True, 'port': 9333})
    monkeypatch.setattr('dp_cli.session._port_open',
                        lambda ip, p, timeout=0.3: False)
    wakes = []
    monkeypatch.setattr(daemon, '_wake_acceptor', lambda s: wakes.append(s))
    daemon._idle_watcher(None, None, ev, 's')
    assert ev.flag is True and wakes == ['s']


def test_idle_watcher_attached_live_port_beyond_extended(monkeypatch):
    """P2：attached+端口活但 idle 超宽限 TTL→仍自退（豁免不是永久）。"""
    import time
    from dp_cli import daemon
    ev = _TickOnceEv()
    monkeypatch.setitem(daemon._state, 'last_activity',
                        time.time() - daemon._IDLE_TTL_ATTACHED - 10)
    monkeypatch.setattr('dp_cli.session.load_session',
                        lambda s: {'user_connected': True, 'port': 9333})
    monkeypatch.setattr('dp_cli.session._port_open',
                        lambda ip, p, timeout=0.3: True)
    wakes = []
    monkeypatch.setattr(daemon, '_wake_acceptor', lambda s: wakes.append(s))
    daemon._idle_watcher(None, None, ev, 's')
    assert ev.flag is True and wakes == ['s']

# ── meta sock_path 权威源（client/server 规则路径失联兜底）──────

def _ep_sock_factory(calls, fail_eps=(), response=b''):
    """记录 connect/connect_ex 端点、按 fail_eps 编程失败的
    socket 桩工厂（sock_path 候选端点断言用）。"""
    fail = frozenset(fail_eps)

    def _factory(family, sock_type, *a, **k):
        calls.append(('socket', family, sock_type))

        class _S:
            _resp = response

            def settimeout(self, t):
                pass

            def setsockopt(self, *a):
                pass

            def connect(self, ep):
                calls.append(('connect', ep))
                if ep in fail:
                    raise OSError('refused')

            def connect_ex(self, ep):
                calls.append(('connect_ex', ep))
                return 111 if ep in fail else 0

            def sendall(self, b):
                calls.append(('sendall', len(b)))

            def recv(self, n):
                d, self._resp = self._resp, b''
                return d

            def close(self):
                pass
        return _S()
    return _factory


def test_send_recv_meta_sock_path_fallback(monkeypatch, tmp_path):
    """sock_path 权威源：规则路径 connect 拒 → meta.sock_path 兜底连。"""
    import json
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    sock, meta = _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    authority = str(tmp_path / 'elsewhere.sock')
    meta.write_text(json.dumps({'pid': 1, 'sock_path': authority}))
    calls = []
    monkeypatch.setattr(
        daemon._socket, 'socket',
        _ep_sock_factory(calls, fail_eps={str(sock)},
                         response=b'{"exit":0,"result":{"ok":1}}\n'))
    r = daemon._send_recv('t', {'argv': ['snapshot']}, 5)
    conns = [c[1] for c in calls if c[0] == 'connect']
    assert conns == [str(sock), authority]       # 规则先试→meta 兜底
    assert r['exit'] == 0


def test_send_recv_rule_path_ok_skips_meta_fallback(
        monkeypatch, tmp_path):
    """规则路径一连即通 → 不触 meta.sock_path 兜底端点。"""
    import json
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    sock, meta = _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    authority = str(tmp_path / 'elsewhere.sock')
    meta.write_text(json.dumps({'pid': 1, 'sock_path': authority}))
    calls = []
    monkeypatch.setattr(
        daemon._socket, 'socket',
        _ep_sock_factory(calls,
                         response=b'{"exit":0,"result":{"ok":1}}\n'))
    r = daemon._send_recv('t', {'argv': ['snapshot']}, 5)
    conns = [c[1] for c in calls if c[0] == 'connect']
    assert conns == [str(sock)]                  # 规则先通→不触兜底
    assert r['exit'] == 0


def test_send_recv_legacy_meta_no_sock_path_rule_only(
        monkeypatch, tmp_path):
    """老 meta 无 sock_path 字段 → 仅规则路径一次 connect（向后兼容）。"""
    import json
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    sock, meta = _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    meta.write_text(json.dumps({'pid': 1}))      # 老 meta 无字段
    calls = []
    monkeypatch.setattr(
        daemon._socket, 'socket',
        _ep_sock_factory(calls,
                         response=b'{"exit":0,"result":{"ok":1}}\n'))
    r = daemon._send_recv('t', {'argv': ['x']}, 5)
    conns = [c[1] for c in calls if c[0] == 'connect']
    assert conns == [str(sock)]
    assert r['exit'] == 0


def test_send_recv_meta_sock_path_same_as_rule_single_attempt(
        monkeypatch, tmp_path):
    """meta.sock_path 与规则一致 → 去重单端点，行为不变。"""
    import json
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    sock, meta = _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    meta.write_text(json.dumps({'pid': 1, 'sock_path': str(sock)}))
    calls = []
    monkeypatch.setattr(
        daemon._socket, 'socket',
        _ep_sock_factory(calls, fail_eps={str(sock)}))
    r = daemon._send_recv('t', {'argv': ['x']}, 5)
    conns = [c[1] for c in calls if c[0] == 'connect']
    assert conns == [str(sock)]                  # 不重试同端点
    assert r[0] == 'err' and r[2] is False       # 未送达语义不变


def test_unix_alive_prefers_meta_sock_path(monkeypatch, tmp_path):
    """探活优先 meta.sock_path：首候选即命中不触规则路径；
    权威路径拒连时兜底规则路径。"""
    import json
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    sock, meta = _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    authority = str(tmp_path / 'elsewhere.sock')
    meta.write_text(json.dumps({'pid': 1, 'sock_path': authority}))
    calls = []
    monkeypatch.setattr(daemon._socket, 'socket',
                        _ep_sock_factory(calls))
    assert daemon._unix_alive('t') is True
    probes = [c[1] for c in calls if c[0] == 'connect_ex']
    assert probes == [authority]                 # 权威路径优先命中
    # 权威路径拒连 → 兜底规则路径
    calls.clear()
    monkeypatch.setattr(daemon._socket, 'socket',
                        _ep_sock_factory(calls, fail_eps={authority}))
    assert daemon._unix_alive('t') is True
    probes = [c[1] for c in calls if c[0] == 'connect_ex']
    assert probes == [authority, str(sock)]


def test_daemon_ready_meta_sock_path_exists(monkeypatch, tmp_path):
    """规则 sock 缺失但 meta.sock_path 指向的文件在 → 不误判死、
    不清孤儿 meta（规则失联≠daemon 死）。"""
    import json
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    sock, meta = _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    authority = tmp_path / 'elsewhere.sock'
    authority.write_text('')
    meta.write_text(
        json.dumps({'pid': 1, 'sock_path': str(authority)}))
    monkeypatch.setattr(daemon, '_unix_alive', lambda s: True)
    assert daemon._daemon_ready('t') is True
    assert not sock.exists()                     # 规则路径本就缺
    assert meta.exists() and authority.exists()  # 未误清


def test_cleanup_stale_removes_meta_sock_path(
        monkeypatch, tmp_path):
    """死标记三件套扩展：meta.sock_path 权威路径文件连带回收，
    防 TMPDIR 下残留死 sock。"""
    import json
    from dp_cli import daemon
    sock, meta = _bind_tmp_marks(monkeypatch, daemon, tmp_path)
    sock.write_text('')
    authority = tmp_path / 'elsewhere.sock'
    authority.write_text('')
    meta.write_text(
        json.dumps({'pid': 1, 'sock_path': str(authority)}))
    daemon._cleanup_stale('t')
    assert not sock.exists() and not meta.exists()
    assert not authority.exists()


def test_run_daemon_meta_writes_sock_path(
        monkeypatch, tmp_path, sock_dir):
    """run_daemon bind 后 meta 落 sock_path 字段=实际 bind 路径。"""
    import json, threading, time
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    monkeypatch.setattr(daemon, '_sock_path',
                        lambda s: sock_dir / f'{s}.sock')
    monkeypatch.setattr(daemon, '_meta_path',
                        lambda s: tmp_path / f'{s}.session')
    monkeypatch.setattr(daemon, '_WATCH_TICK', 0.02)
    monkeypatch.setattr('dp_cli.session.load_session', lambda s: {})
    t = threading.Thread(target=daemon.run_daemon, args=('t',),
                         daemon=True)
    t.start()
    meta = tmp_path / 't.session'
    for _ in range(150):
        if meta.exists():
            break
        time.sleep(0.02)
    m = json.loads(meta.read_text())
    assert m['sock_path'] == str(sock_dir / 't.sock')
    assert m['socket'] == m['sock_path']         # 兼容字段同值
    monkeypatch.setattr(daemon, '_IDLE_TTL', 0)  # 触发 idle 自退
    t.join(timeout=10)
    assert not t.is_alive()


def test_run_daemon_meta_writes_version(
        monkeypatch, tmp_path, sock_dir, capsys):
    """run_daemon meta 落 version——daemon-status/_version_probe
    消费链（t2 实测 version:null 修复）；_status 透出同值非空。"""
    import json, threading, time
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    monkeypatch.setattr(daemon, '_sock_path',
                        lambda s: sock_dir / f'{s}.sock')
    monkeypatch.setattr(daemon, '_meta_path',
                        lambda s: tmp_path / f'{s}.session')
    monkeypatch.setattr(daemon, '_WATCH_TICK', 0.02)
    monkeypatch.setattr('dp_cli.session.load_session', lambda s: {})
    t = threading.Thread(target=daemon.run_daemon, args=('t',),
                         daemon=True)
    t.start()
    meta = tmp_path / 't.session'
    for _ in range(150):
        if meta.exists():
            break
        time.sleep(0.02)
    m = json.loads(meta.read_text())
    assert m.get('version')                        # 非 null 非缺键
    assert m['version'] == daemon._dpx_version()
    # _status 同链回读——meta.sock_path 权威源直达，version 透出
    daemon._status('t')
    out = json.loads(capsys.readouterr().out)
    assert out['data']['running'] is True
    assert out['data']['version'] == m['version']
    monkeypatch.setattr(daemon, '_IDLE_TTL', 0)   # 触发 idle 自退
    t.join(timeout=10)
    assert not t.is_alive()
