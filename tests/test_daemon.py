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


def test_run_daemon_idle_self_exit_e2e(monkeypatch, tmp_path):
    """P1-3 e2e：idle 超时→wake 连接→accept 返回看到 stop_ev→
    finally 清理 sock/meta——真 socket 全链路。"""
    import threading, time
    from dp_cli import daemon
    if not daemon._HAS_UNIX_SOCK:
        pytest.skip('POSIX only')
    monkeypatch.setattr(daemon, '_IDLE_TTL', 0)       # 立即超时
    monkeypatch.setattr(daemon, '_WATCH_TICK', 0.02)
    monkeypatch.setattr(daemon, '_sock_path',
                        lambda s: tmp_path / f'{s}.sock')
    monkeypatch.setattr(daemon, '_meta_path',
                        lambda s: tmp_path / f'{s}.session')
    monkeypatch.setattr('dp_cli.session.load_session', lambda s: {})
    t = threading.Thread(target=daemon.run_daemon, args=('t',),
                         daemon=True)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive()                            # 正常退出非挂死
    assert not (tmp_path / 't.sock').exists()          # finally 清理
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
