"""
chrome://inspect 桥接进程生命周期管理

- detect_inspect_mode(port): 判断一个 CDP 端点是否 chrome://inspect 限流模式
  （有 /json/version 但没有 /json / HTTP REST）。这是 Chrome 144+ 开启
  "Allow remote debugging for this browser instance" 后的特征。

- start_bridge(user_data_dir): spawn `python -m dp_cli.bridge` 子进程，
  等待其向 stdout 打印 "BRIDGE_READY host=... port=..." 标记后返回 (pid, port)。

- stop_bridge(pid): 向子进程发终止信号；如 2 秒未退出再强杀。
  POSIX: SIGTERM → SIGKILL（针对整个进程组）。
  Windows: CTRL_BREAK_EVENT → taskkill /F /T。
- is_bridge_alive(pid): OS 级存在性检查（Windows 走 OpenProcess）。
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

IS_WINDOWS = sys.platform == 'win32'


def _detach_spawn_kwargs() -> dict:
    """让 bridge 子进程脱离父进程的信号/控制台分组。

    POSIX: ``start_new_session=True`` → setsid，使 bridge 自成进程组，
    父进程 Ctrl-C 不会传递过来。
    Windows: ``CREATE_NEW_PROCESS_GROUP`` 让我们后续可以发 CTRL_BREAK_EVENT；
    ``CREATE_NO_WINDOW`` 避免在 GUI/服务环境弹出黑色控制台窗口。
    """
    if IS_WINDOWS:
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        return {'creationflags': CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW}
    return {'start_new_session': True}


def _win_pid_alive(pid: int) -> bool:
    """Windows: 用 OpenProcess + GetExitCodeProcess 判断进程是否存活。"""
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(h)


def _win_terminate(pid: int) -> None:
    """Windows: taskkill /F /T 强杀进程及其子进程树。"""
    try:
        subprocess.run(
            ['taskkill', '/F', '/T', '/PID', str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except Exception:
        pass


_READY_RE = re.compile(r'^BRIDGE_READY host=(?P<host>\S+) port=(?P<port>\d+)\s*$')


def detect_inspect_mode(port: int, timeout: float = 2.0) -> bool:
    """探测 127.0.0.1:port 是否 chrome://inspect 限流模式。

    实测 Chrome 144+ 的 chrome://inspect 远程调试模式：
      - TCP 端口正常监听，且 browser-level WebSocket 可连
      - 但所有 HTTP REST 端点（/json, /json/version, /json/list 等）
        一律返回 404（无 DevTools HTTP 服务器）

    经典 ``--remote-debugging-port`` 模式：
      - /json/version → 200 OK + JSON（含 Browser/Protocol-Version）
      - /json → 200 OK + JSON 数组

    判定规则：/json/version 返回合法 JSON → 经典模式（False）；
              否则即 inspect 模式（True）。
    """
    try:
        import requests
    except ImportError:
        return False
    base = f'http://127.0.0.1:{port}'
    try:
        r = requests.get(f'{base}/json/version', timeout=timeout)
    except Exception:
        # 端口不通属于异常情况；保守返回 False 让上层报错
        return False
    if r.status_code != 200:
        return True
    try:
        data = r.json()
        # 经典模式 /json/version 返回 dict，含 Browser 字段
        if isinstance(data, dict) and 'Browser' in data:
            return False
    except Exception:
        pass
    return True


def start_bridge(user_data_dir: str | os.PathLike,
                 listen_port: int = 0,
                 ready_timeout: float = 90.0) -> tuple[int, int]:
    """启动 bridge 子进程，等待其就绪后返回 (pid, actual_port)。

    子进程在就绪时会向 stdout 打印 "BRIDGE_READY host=... port=..." 一行。
    如果 ``listen_port=0``，由 OS 分配随机端口，实际端口在该行里。

    :param user_data_dir: Chrome 用户数据目录（含 DevToolsActivePort）
    :param listen_port: 0 表示随机；否则尝试绑定指定端口
    :param ready_timeout: 等待 ready 的秒数，首次连接用户需要点 Allow
    :raises RuntimeError: 子进程启动失败或超时
    """
    cmd = [
        sys.executable, '-m', 'dp_cli.bridge',
        '--user-data-dir', str(user_data_dir),
        '--listen', str(listen_port),
        '-v',
    ]
    # 让 bridge 成为独立进程组，防止父进程 SIGINT/Ctrl-C 误杀。
    # POSIX 走 start_new_session；Windows 走 CREATE_NEW_PROCESS_GROUP。
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # 行缓冲
        **_detach_spawn_kwargs(),
    )

    # 立即提示用户：bridge 正在连接；若 Chrome 弹出授权框请点击。
    # 写 stderr 避免污染 stdout 的 JSON 输出。
    print('[dp] 正在启动 bridge 并连接浏览器（Chrome/Edge）…',
          file=sys.stderr, flush=True)
    print('[dp] 💡 若浏览器弹出 "Allow remote debugging" 对话框，'
          '请切到浏览器窗口点击 "Allow"（后续命令会自动复用连接）',
          file=sys.stderr, flush=True)

    # 异步读 stderr，方便 timeout 时回显给用户
    import threading
    stderr_buf: list[str] = []
    stderr_lock = threading.Lock()

    def _read_stderr():
        try:
            for line in proc.stderr:
                with stderr_lock:
                    stderr_buf.append(line)
        except Exception:
            pass

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()

    def _collect_stderr(max_chars: int = 4000) -> str:
        with stderr_lock:
            s = ''.join(stderr_buf)
        if len(s) > max_chars:
            s = '...(truncated)...\n' + s[-max_chars:]
        return s

    # 读 stdout 直到出现 BRIDGE_READY，或超时，或进程退出
    deadline = time.monotonic() + ready_timeout
    actual_port: int | None = None
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(
                    f'bridge 子进程提前退出 (code={proc.returncode})。stderr:\n{_collect_stderr()}'
                )
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.05)
                continue
            m = _READY_RE.match(line.strip())
            if m:
                actual_port = int(m.group('port'))
                break
        if actual_port is None:
            proc.terminate()
            raise RuntimeError(
                f'bridge 启动超时 ({ready_timeout}s)。\n'
                f'若是首次连接：请把浏览器窗口（Chrome/Edge）切到前台，点击弹出的'
                f' "Allow remote debugging for this browser instance" 对话框。\n'
                f'当前 bridge stderr:\n{_collect_stderr()}'
            )
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
        raise

    # ready 后让 bridge 的 stdout 继续被 drain，避免管道阻塞。
    # stderr 已有 stderr_thread 在持续消费，继续复用即可。
    def _drain(stream):
        try:
            for _ in stream:
                pass
        except Exception:
            pass

    if proc.stdout:
        threading.Thread(target=_drain, args=(proc.stdout,), daemon=True).start()

    return proc.pid, actual_port


def is_bridge_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        return _win_pid_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 进程存在但没权限，仍视为活
    except Exception:
        return False


def stop_bridge(pid: int, timeout: float = 2.0) -> bool:
    """停止 bridge 子进程。返回是否成功终止。

    POSIX: SIGTERM 整个进程组 → 等待 → SIGKILL。
    Windows: CTRL_BREAK_EVENT（依赖 spawn 时的 CREATE_NEW_PROCESS_GROUP）
             → 等待 → taskkill /F /T 终止进程树。
    """
    if not is_bridge_alive(pid):
        return True

    if IS_WINDOWS:
        # 1) 优雅: 给整个进程组发 CTRL_BREAK_EVENT
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
        except Exception:
            pass

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not is_bridge_alive(pid):
                return True
            time.sleep(0.05)

        # 2) 强杀: taskkill /F /T 终止进程树
        _win_terminate(pid)
        time.sleep(0.1)
        return not is_bridge_alive(pid)

    # POSIX: 先 SIGTERM 整个进程组（start_new_session 让 bridge 自成组）
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_bridge_alive(pid):
            return True
        time.sleep(0.05)

    # 强杀
    try:
        os.killpg(pid, signal.SIGKILL)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    time.sleep(0.1)
    return not is_bridge_alive(pid)
