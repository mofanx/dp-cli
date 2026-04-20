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

    注意：stop_bridge 的返回值依赖 is_bridge_alive(pid)，而后者用 os.kill(pid,0)
    探测，对“未 reap 的僵尸进程”会误判为活着。生产流程中 bridge 是上一次 dp 调用
    spawn 的孤儿（start_new_session + 父进程早退出 → 被 init 收尸），所以
    is_bridge_alive 会正确返回 False。本测试里 Popen 还持有子进程，我们用
    proc.poll() 显式 reap 以模拟生产环境再断言。
    """
    proc = subprocess.Popen(
        [sys.executable, '-c', 'import time; time.sleep(60)'],
        start_new_session=True,
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
