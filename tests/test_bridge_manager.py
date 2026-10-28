"""
bridge_manager 单元测试

重点：
  - detect_inspect_mode：classic / inspect / 端口不通 三态判定
  - is_bridge_alive / stop_bridge：对任意 shell 子进程
  - start_bridge 提前退出时的错误报告（使用一个立即退出的假 bridge cmd）
"""
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from dp_cli import bridge_manager


# ─────────────────────────────────────────────────────────────────────────────
# 小型假 HTTP 服务：可配置 /json/version 的返回
# ─────────────────────────────────────────────────────────────────────────────

class _FakeHandler(BaseHTTPRequestHandler):
    mode: str = 'classic'  # class-level, 每个测试替换

    def log_message(self, *args, **kwargs):
        pass  # 静默

    def do_GET(self):
        if self.path == '/json/version':
            if self.mode == 'classic':
                body = (b'{"Browser":"Chrome/147.0.0","Protocol-Version":"1.3",'
                        b'"User-Agent":"Mozilla/5.0"}')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.mode == 'inspect':
                self.send_response(404)
                self.send_header('Content-Length', '0')
                self.end_headers()
            elif self.mode == 'not_json':
                body = b'hello not json'
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


class _FakeServer:
    def __init__(self, mode: str):
        self.mode = mode
        handler_cls = type('H', (_FakeHandler,), {'mode': mode})
        self.server = HTTPServer(('127.0.0.1', 0), handler_cls)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


# ─────────────────────────────────────────────────────────────────────────────
# detect_inspect_mode
# ─────────────────────────────────────────────────────────────────────────────

def test_detect_classic_mode():
    srv = _FakeServer('classic')
    try:
        assert bridge_manager.detect_inspect_mode(srv.port) is False
    finally:
        srv.stop()


def test_detect_inspect_mode_404():
    srv = _FakeServer('inspect')
    try:
        assert bridge_manager.detect_inspect_mode(srv.port) is True
    finally:
        srv.stop()


def test_detect_inspect_mode_not_json():
    """200 OK 但不是 JSON / 缺 Browser 字段也视为 inspect（保守判定）。"""
    srv = _FakeServer('not_json')
    try:
        assert bridge_manager.detect_inspect_mode(srv.port) is True
    finally:
        srv.stop()


def test_detect_port_closed():
    """端口不通：保守返回 False（让上层真实连接报错）。"""
    # 找一个几乎肯定没人用的端口
    assert bridge_manager.detect_inspect_mode(1, timeout=0.5) is False


# ─────────────────────────────────────────────────────────────────────────────
# is_bridge_alive / stop_bridge
# ─────────────────────────────────────────────────────────────────────────────

def test_alive_and_stop_real_subprocess():
    """验证 stop_bridge 的信号确实让进程退出。

    注意（POSIX）：stop_bridge 的返回值依赖 is_bridge_alive(pid)，而后者用
    os.kill(pid,0) 探测，对"未 reap 的僵尸进程"会误判为活着。生产流程中
    bridge 是上一次 dp 调用 spawn 的孤儿（start_new_session + 父进程早退出
    → 被 init 收尸），所以 is_bridge_alive 会正确返回 False。本测试里 Popen
    还持有子进程，我们用 proc.poll() 显式 reap 以模拟生产环境再断言。

    Windows: is_bridge_alive 走 OpenProcess + GetExitCodeProcess，没有僵尸
    进程问题；stop_bridge 用 CTRL_BREAK_EVENT + taskkill /F /T 终止。
    """
    proc = subprocess.Popen(
        [sys.executable, '-c', 'import time; time.sleep(60)'],
        **bridge_manager._detach_spawn_kwargs(),
    )
    try:
        assert bridge_manager.is_bridge_alive(proc.pid) is True
        # 调用 stop_bridge（忽略返回值，原因见 docstring）
        bridge_manager.stop_bridge(proc.pid, timeout=2)
        # 轮询 Popen 观察进程确实已退出（≈reap 僵尸）
        for _ in range(40):
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        assert proc.poll() is not None, '子进程未响应 stop_bridge 的信号'
        # reap 后 is_bridge_alive 应返回 False
        assert bridge_manager.is_bridge_alive(proc.pid) is False
    finally:
        try:
            proc.kill()
            proc.wait(timeout=1)
        except Exception:
            pass


def test_alive_zero_pid():
    assert bridge_manager.is_bridge_alive(0) is False
    assert bridge_manager.is_bridge_alive(-1) is False


def test_stop_nonexistent_pid_noop():
    # 一个基本不可能正在跑的 pid
    assert bridge_manager.stop_bridge(2_147_483_646) is True


# ─────────────────────────────────────────────────────────────────────────────
# start_bridge 异常路径
# ─────────────────────────────────────────────────────────────────────────────

def test_start_bridge_missing_profile(tmp_path):
    """user-data-dir 下没 DevToolsActivePort → bridge 子进程立即异常退出，
    start_bridge 应包含 stderr 回显。"""
    bogus = tmp_path / 'nochrome'
    bogus.mkdir()
    with pytest.raises(RuntimeError) as exc:
        bridge_manager.start_bridge(bogus, listen_port=0, ready_timeout=10)
    msg = str(exc.value)
    # 错误信息应含 stderr（aiohttp/websocket 启动前 DevToolsActivePort 读取失败会抛）
    assert 'bridge' in msg.lower() or 'stderr' in msg.lower()


def test_start_bridge_timeout_enforced_pending_upstream(tmp_path):
    """上游 WS 握手永久 pending（Chrome 授权框没人点的场景）→
    start_bridge 必须在 ready_timeout 内放弃。

    回归保护：旧实现用 proc.stdout.readline() 同步等 READY——授权等待
    期间 stdout 静默，readline 阻塞导致循环内 notify/escalate/poll/
    deadline 检查全部失效，ready_timeout 名存实亡（实测挂在子进程
    ws open_timeout=120s 上）。改队列读取后 deadline 才真生效。"""
    import socket

    # 假 CDP 端口：accept TCP 但从不回应 WS upgrade
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    stop_flag = []
    held = []          # 持有连接防 GC 关闭——握手必须永远 pending

    def _hold():
        while not stop_flag:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            try:
                c.recv(4096)   # 读到 upgrade 请求但绝不回包
                held.append(c)
            except Exception:
                pass

    threading.Thread(target=_hold, daemon=True).start()

    udd = tmp_path / 'fake-profile'
    udd.mkdir()
    (udd / 'DevToolsActivePort').write_text(f'{port}\n/devtools/browser/x\n')

    t0 = time.perf_counter()
    try:
        with pytest.raises(RuntimeError, match='启动超时'):
            bridge_manager.start_bridge(udd, listen_port=0, ready_timeout=3)
        # 队列改造前会挂 ~120s（子进程 ws open_timeout）
        assert time.perf_counter() - t0 < 15
    finally:
        stop_flag.append(1)
        srv.close()


def test_bridge_kill_posix_getattr_guard(monkeypatch):
    """CTRL_BREAK_EVENT getattr 守卫——POSIX 缺常量时不 NameError。"""
    import signal
    # POSIX signal 模块本无 CTRL_BREAK_EVENT——monkeypatch 删掉模拟
    if hasattr(signal, 'CTRL_BREAK_EVENT'):
        monkeypatch.delattr(signal, 'CTRL_BREAK_EVENT', raising=False)
    import dp_cli.bridge_manager as bm
    # 惰性取值只发生在 IS_WINDOWS 分支——POSIX 侧确认不触达常量
    assert getattr(signal, 'CTRL_BREAK_EVENT',
                   getattr(signal, 'SIGTERM', 15)) == signal.SIGTERM


def test_ctrl_break_getattr_fallback(monkeypatch):
    """CTRL_BREAK_EVENT getattr 兜底——POSIX 上 mock win 分支真实调用,
    常量缺失时落 SIGTERM 而非 AttributeError(不断言常量存在性)。"""
    import signal
    from dp_cli import bridge_manager

    monkeypatch.setattr(bridge_manager, 'IS_WINDOWS', True)
    monkeypatch.setattr(bridge_manager.os, 'name', 'posix')
    # POSIX signal 本就无 CTRL_BREAK_EVENT——真实走 getattr 兜底
    assert not hasattr(signal, 'CTRL_BREAK_EVENT')
    kills = []
    monkeypatch.setattr(bridge_manager.os, 'kill',
                        lambda pid, sig: kills.append(sig))
    alive = iter([True, False])
    monkeypatch.setattr(bridge_manager, 'is_bridge_alive',
                        lambda pid: next(alive, False))
    assert bridge_manager.stop_bridge(999, timeout=0.2) is True
    assert kills == [signal.SIGTERM], kills


# ─────────────────────────────────────────────────────────────────────────────
# P1: killpg pid 复用防护 / _reap_proc / spawn 后 wait 线程
# ─────────────────────────────────────────────────────────────────────────────

def test_pid_cmdline_is_bridge_proc_hit(monkeypatch):
    """/proc cmdline 含 'dp_cli'+'bridge' → True。"""
    from pathlib import Path
    monkeypatch.setattr(Path, 'is_file', lambda self: True)
    monkeypatch.setattr(
        Path, 'read_bytes',
        lambda self: b'/usr/bin/python\x00-m\x00dp_cli.bridge\x00'
                     b'--user-data-dir\x00/x\x00')
    assert bridge_manager._pid_cmdline_is_bridge(999) is True


def test_pid_cmdline_is_bridge_proc_other(monkeypatch):
    """/proc cmdline 可读但不是 bridge（pid 被无关进程复用）→ False。"""
    from pathlib import Path
    monkeypatch.setattr(Path, 'is_file', lambda self: True)
    monkeypatch.setattr(Path, 'read_bytes', lambda self: b'bash\x00')
    assert bridge_manager._pid_cmdline_is_bridge(999) is False


def test_pid_cmdline_is_bridge_ps_fallback(monkeypatch):
    """无 /proc（macOS 场景）→ ps -o command= 回退，命中 bridge → True。"""
    from pathlib import Path
    monkeypatch.setattr(Path, 'is_file', lambda self: False)

    def fake_run(cmd, **kw):
        assert cmd[:2] == ['ps', '-p']
        class R:
            returncode = 0
            stdout = 'python -m dp_cli.bridge --user-data-dir /x'
        return R()
    monkeypatch.setattr(bridge_manager.subprocess, 'run', fake_run)
    assert bridge_manager._pid_cmdline_is_bridge(999) is True


def test_pid_cmdline_is_bridge_unreadable_returns_false(monkeypatch):
    """/proc 不可读且 ps 也失败 → False（保守：调用点退化单进程信号，
    绝不冒险 killpg）。"""
    from pathlib import Path
    monkeypatch.setattr(Path, 'is_file', lambda self: False)
    monkeypatch.setattr(bridge_manager.subprocess, 'run',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('no ps')))
    assert bridge_manager._pid_cmdline_is_bridge(999) is False


def test_stop_bridge_non_bridge_cmdline_skips_killpg(monkeypatch):
    """pid 复用防护核心断言：cmdline 校验非 bridge → 不走 killpg，
    TERM/KILL 都退化为单进程 os.kill。"""
    import signal
    killpg_calls, kill_calls = [], []
    monkeypatch.setattr(bridge_manager.os, 'killpg',
                        lambda p, s: killpg_calls.append((p, s)))
    monkeypatch.setattr(bridge_manager.os, 'kill',
                        lambda p, s: kill_calls.append((p, s)))
    monkeypatch.setattr(bridge_manager, 'is_bridge_alive',
                        lambda pid: True)          # 一直"活"→走完 TERM+KILL
    monkeypatch.setattr(bridge_manager, '_pid_cmdline_is_bridge',
                        lambda pid: False)
    monkeypatch.setattr(bridge_manager, 'IS_WINDOWS', False)

    assert bridge_manager.stop_bridge(12345, timeout=0.2) is False
    assert killpg_calls == []
    assert (12345, signal.SIGTERM) in kill_calls
    assert (12345, signal.SIGKILL) in kill_calls


def test_stop_bridge_bridge_cmdline_uses_killpg(monkeypatch):
    """cmdline 确认是 bridge → 正常走 killpg 杀整个进程组。"""
    import signal
    killpg_calls, kill_calls = [], []
    monkeypatch.setattr(bridge_manager.os, 'killpg',
                        lambda p, s: killpg_calls.append((p, s)))
    monkeypatch.setattr(bridge_manager.os, 'kill',
                        lambda p, s: kill_calls.append((p, s)))
    alive = iter([True, False])   # TERM 后第一次探活即死
    monkeypatch.setattr(bridge_manager, 'is_bridge_alive',
                        lambda pid: next(alive, True))
    monkeypatch.setattr(bridge_manager, '_pid_cmdline_is_bridge',
                        lambda pid: True)
    monkeypatch.setattr(bridge_manager, 'IS_WINDOWS', False)

    assert bridge_manager.stop_bridge(12345, timeout=0.5) is True
    assert (12345, signal.SIGTERM) in killpg_calls
    assert kill_calls == []       # killpg 成功无 fallback


def test_reap_proc_terminate_then_wait(monkeypatch):
    """_reap_proc：terminate 后 wait 成功 → 不再 kill。"""
    from unittest.mock import Mock
    proc = Mock()
    bridge_manager._reap_proc(proc)
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once()
    proc.kill.assert_not_called()


def test_reap_proc_wait_timeout_falls_back_kill(monkeypatch):
    """_reap_proc：wait 超时 → kill + 再 wait 兜底。"""
    from unittest.mock import Mock
    proc = Mock()
    proc.wait.side_effect = [subprocess.TimeoutExpired('c', 3), None]
    bridge_manager._reap_proc(proc)
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()
    assert proc.wait.call_count == 2


def test_start_bridge_failure_reaps_proc(monkeypatch, tmp_path):
    """spawn 失败路径：terminate 后必须 wait 回收（防 zombie），
    wait 超时再 kill。"""
    from unittest.mock import Mock
    proc = Mock()
    proc.poll.return_value = None
    proc.stderr = iter([])
    proc.stdout = iter([])
    proc.wait.side_effect = subprocess.TimeoutExpired('cmd', 3)
    monkeypatch.setattr(bridge_manager.subprocess, 'Popen',
                        lambda *a, **k: proc)
    monkeypatch.setattr(bridge_manager, '_detach_spawn_kwargs', lambda: {})

    with pytest.raises(RuntimeError):
        bridge_manager.start_bridge(tmp_path, ready_timeout=0.5)
    proc.terminate.assert_called()
    proc.wait.assert_called()
    proc.kill.assert_called()


def test_start_bridge_success_registers_wait_thread(monkeypatch, tmp_path):
    """spawn 成功路径：注册 daemon 线程执行 proc.wait() 回收退出码。"""
    from unittest.mock import Mock
    proc = Mock()
    proc.poll.return_value = None
    proc.pid = 4242
    proc.stderr = iter([])
    proc.stdout = iter(['BRIDGE_READY host=127.0.0.1 port=4567\n'])

    started_targets = []
    real_thread = threading.Thread
    class SpyThread(real_thread):
        def start(self):
            started_targets.append(getattr(self, '_target', None))
            return super().start()

    monkeypatch.setattr(bridge_manager.subprocess, 'Popen',
                        lambda *a, **k: proc)
    monkeypatch.setattr(bridge_manager, '_detach_spawn_kwargs', lambda: {})
    monkeypatch.setattr(threading, 'Thread', SpyThread)

    pid, port = bridge_manager.start_bridge(tmp_path, ready_timeout=5)
    assert (pid, port) == (4242, 4567)
    assert proc.wait in started_targets


# ─────────────────────────────────────────────────────────────────────────────
# P2：bridge registry 无 TTL——读侧惰性清理
# ─────────────────────────────────────────────────────────────────────────────

def test_registry_iter_entries_lazy_prune_dead_pid(tmp_path, monkeypatch):
    """P2：stale bridge 条目堆积——iter_entries 读侧过滤死 pid/
    残缺项并惰性 unlink 回写清理；活 pid 保留。"""
    import json
    from dp_cli import bridge_registry as reg
    monkeypatch.setattr(reg, '_bridges_dir', lambda: tmp_path)
    reg.register_bridge('/p/a', pid=99999991, port=40111, real_port=1)
    reg.register_bridge('/p/b', pid=99999992, port=40222, real_port=2)
    (tmp_path / 'orphan.json').write_text(
        json.dumps({'port': 9}), encoding='utf-8')      # 无 pid 残缺项
    # mock _pid_alive：a 死 b 活
    monkeypatch.setattr(reg, '_pid_alive',
                        lambda p: int(p) != 99999991)
    # cmdline 不可考 → 活 pid 保守保留
    monkeypatch.setattr('dp_cli.daemon._read_cmdline', lambda p: None)
    entries = reg.iter_entries()
    assert [e.get('pid') for e in entries] == [99999992]
    assert not reg._entry_path('/p/a').exists()         # 死件惰性清除
    assert not (tmp_path / 'orphan.json').exists()
    assert reg._entry_path('/p/b').exists()


def test_registry_iter_entries_prunes_pid_reuse(tmp_path, monkeypatch):
    """P2：pid 复用——pid 活但 cmdline 可读且确认非 dp_cli bridge
    → 按 stale 清；cmdline 读不出的活 pid 保守保留。"""
    import os
    if os.name != 'posix':
        pytest.skip('POSIX cmdline 分支')
    from dp_cli import bridge_registry as reg
    import dp_cli.daemon as dm
    monkeypatch.setattr(reg, '_bridges_dir', lambda: tmp_path)
    reg.register_bridge('/p/c', pid=8888, port=1, real_port=1)
    reg.register_bridge('/p/d', pid=8889, port=2, real_port=2)
    monkeypatch.setattr(reg, '_pid_alive', lambda p: True)
    monkeypatch.setattr(dm, '_read_cmdline',
                        lambda p: ('/usr/sbin/sshd -D' if p == 8888
                                   else None))         # d: 读不出→保留
    entries = reg.iter_entries()
    assert [e.get('pid') for e in entries] == [8889]
    assert not reg._entry_path('/p/c').exists()         # 复用 stale 已清
    assert reg._entry_path('/p/d').exists()


def test_registry_iter_entries_probe_failure_conservative(
        tmp_path, monkeypatch):
    """P2：探活本身异常（is_bridge_alive 抛错）→ _pid_alive
    保守 True 不误清活条目。"""
    from dp_cli import bridge_registry as reg
    monkeypatch.setattr(reg, '_bridges_dir', lambda: tmp_path)
    reg.register_bridge('/p/e', pid=7777, port=1, real_port=1)
    monkeypatch.setattr(
        'dp_cli.bridge_manager.is_bridge_alive',
        lambda p: (_ for _ in ()).throw(RuntimeError('x')))
    monkeypatch.setattr('dp_cli.daemon._read_cmdline', lambda p: None)
    entries = reg.iter_entries()
    assert [e.get('pid') for e in entries] == [7777]
    assert reg._entry_path('/p/e').exists()
