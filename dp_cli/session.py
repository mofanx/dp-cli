# -*- coding:utf-8 -*-
"""
dp-cli 会话管理模块
通过固定端口连接复用已运行的浏览器实例，实现跨命令状态共享。
"""
import json
import os
import sys
from pathlib import Path
from time import sleep, perf_counter


# 会话状态文件默认目录
_SESSION_DIR = Path.home() / '.dp_cli' / 'sessions'


def get_session_dir() -> Path:
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return _SESSION_DIR


def get_session_file(name: str) -> Path:
    return get_session_dir() / f'{name}.json'


def load_session(name: str) -> dict:
    """读取会话信息"""
    f = get_session_file(name)
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_session(name: str, data: dict) -> None:
    """保存会话信息"""
    get_session_file(name).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'
    )


def delete_session(name: str) -> bool:
    """删除会话文件"""
    f = get_session_file(name)
    if f.exists():
        f.unlink()
        return True
    return False


def list_sessions() -> list:
    """列出所有会话"""
    d = get_session_dir()
    sessions = []
    for f in d.glob('*.json'):
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            sessions.append({'name': f.stem, **data})
        except Exception:
            pass
    return sessions


def _detect_headless(port: int) -> bool:
    """通过 /json/version 探测浏览器是否 headless 模式"""
    try:
        import requests
        resp = requests.get(f'http://127.0.0.1:{port}/json/version', timeout=3)
        return 'headless' in resp.json().get('User-Agent', '').lower()
    except Exception:
        return False


# ── chrome://inspect 远程调试：端口自动发现 ─────────────────────────────────
#
# Chrome 144+ 支持用户在 chrome://inspect/#remote-debugging 点击
# "Allow remote debugging for this browser instance"，无需 --remote-debugging-port
# 启动参数。开启后 Chrome 会在 user-data-dir 下写入 DevToolsActivePort 文件：
#   第一行：分配的端口号
#   第二行：WebSocket 路径（如 /devtools/browser/<uuid>）
# 我们读取该文件的端口号后，走现有的 set_local_port 连接路径即可。


def default_user_data_dir_for_channel(channel: str = 'stable') -> Path | None:
    """返回指定 channel 的 Chrome 默认 user-data-dir。

    :param channel: stable / beta / dev / canary / chromium
    :return: 用户数据目录 Path；不存在时返回 None
    """
    home = Path.home()
    candidates: dict[str, list[Path]] = {}
    if sys.platform.startswith('linux'):
        candidates = {
            'stable':   [home / '.config' / 'google-chrome'],
            'beta':     [home / '.config' / 'google-chrome-beta'],
            'dev':      [home / '.config' / 'google-chrome-unstable'],
            'canary':   [home / '.config' / 'google-chrome-canary'],
            'chromium': [home / '.config' / 'chromium'],
        }
    elif sys.platform == 'darwin':
        base = home / 'Library' / 'Application Support'
        candidates = {
            'stable':   [base / 'Google' / 'Chrome'],
            'beta':     [base / 'Google' / 'Chrome Beta'],
            'dev':      [base / 'Google' / 'Chrome Dev'],
            'canary':   [base / 'Google' / 'Chrome Canary'],
            'chromium': [base / 'Chromium'],
        }
    elif sys.platform.startswith('win'):
        local = Path(os.environ.get('LOCALAPPDATA', home / 'AppData' / 'Local'))
        candidates = {
            'stable':   [local / 'Google' / 'Chrome' / 'User Data'],
            'beta':     [local / 'Google' / 'Chrome Beta' / 'User Data'],
            'dev':      [local / 'Google' / 'Chrome Dev' / 'User Data'],
            'canary':   [local / 'Google' / 'Chrome SxS' / 'User Data'],
            'chromium': [local / 'Chromium' / 'User Data'],
        }

    for p in candidates.get(channel, []):
        if p.exists():
            return p
    return None


def discover_port_from_profile(user_data_dir: str | os.PathLike) -> int:
    """从 Chrome user-data-dir 的 DevToolsActivePort 文件读取端口号。

    要求 Chrome 144+ 已在 chrome://inspect/#remote-debugging 点击
    "Allow remote debugging for this browser instance" 启用远程调试。

    :raise FileNotFoundError: DevToolsActivePort 文件不存在（远程调试未开启）
    :raise ValueError: 文件内容格式异常
    """
    p = Path(user_data_dir) / 'DevToolsActivePort'
    if not p.exists():
        raise FileNotFoundError(
            f'未找到 {p}。请确认：\n'
            f'  1. 浏览器正在运行，且 --user-data-dir 匹配：{user_data_dir}\n'
            f'  2. 已在地址栏打开 chrome://inspect/#remote-debugging 并点击\n'
            f'     "Allow remote debugging for this browser instance"（需 Chrome 144+）'
        )
    lines = [l.strip() for l in p.read_text(encoding='utf-8').splitlines() if l.strip()]
    if not lines:
        raise ValueError(f'{p} 为空')
    try:
        port = int(lines[0])
    except ValueError as e:
        raise ValueError(f'{p} 第一行不是有效端口号: {lines[0]!r}') from e
    if port <= 0 or port > 65535:
        raise ValueError(f'{p} 端口号越界: {port}')
    return port


def _connect_via_bridge(session_name: str, probe_dir: str, sess: dict):
    """在 probe_dir 模式下自动检测 inspect 限流，起 bridge，返回 ChromiumPage。

    失败时抛异常，由调用方决定 fallback。成功时会更新并保存 session：
      sess['port']        = 若直连则真端口；若起了 bridge 则 bridge 端口
      sess['real_port']   = 真 Chrome 端口（诊断用）
      sess['bridge_pid']  = bridge 子进程 pid（如起了）
      sess['bridge_port'] = bridge 监听端口（= sess['port']）
    """
    from DrissionPage import ChromiumPage
    from DrissionPage._configs.chromium_options import ChromiumOptions
    from dp_cli.bridge_manager import (detect_inspect_mode, start_bridge,
                                        is_bridge_alive, stop_bridge)

    real_port = discover_port_from_profile(probe_dir)

    # 已有 bridge 且仍存活 + 端口有效 → 直接复用
    old_bridge_pid = sess.get('bridge_pid')
    old_bridge_port = sess.get('bridge_port')
    old_real_port = sess.get('real_port')
    if (old_bridge_pid and old_bridge_port and is_bridge_alive(old_bridge_pid)
            and old_real_port == real_port):
        try:
            co = ChromiumOptions(read_file=False)
            co.set_local_port(old_bridge_port)
            co.existing_only(True)
            return ChromiumPage(co)
        except Exception:
            # 复用失败，停旧的再重起
            stop_bridge(old_bridge_pid)

    # 老 bridge 存在但 real_port 变了（Chrome 重启）→ 先清掉
    if old_bridge_pid and is_bridge_alive(old_bridge_pid):
        stop_bridge(old_bridge_pid)

    # 检测真端口是否 inspect 限流模式
    is_inspect = detect_inspect_mode(real_port)

    if not is_inspect:
        # 经典 --remote-debugging-port 模式：直连真端口即可
        co = ChromiumOptions(read_file=False)
        co.set_local_port(real_port)
        co.existing_only(True)
        if _detect_headless(real_port):
            co.headless(True)
        page = ChromiumPage(co)
        sess.pop('bridge_pid', None)
        sess.pop('bridge_port', None)
        sess['port'] = real_port
        sess['real_port'] = real_port
        save_session(session_name, sess)
        return page

    # inspect 模式：起 bridge 代理
    pid, bport = start_bridge(probe_dir)
    try:
        co = ChromiumOptions(read_file=False)
        co.set_local_port(bport)
        co.existing_only(True)
        page = ChromiumPage(co)
    except Exception:
        stop_bridge(pid)
        raise

    sess['port'] = bport
    sess['real_port'] = real_port
    sess['bridge_pid'] = pid
    sess['bridge_port'] = bport
    save_session(session_name, sess)
    return page


def get_browser(session_name: str = 'default', headless: bool = False,
                browser_path: str = None, user_data_dir: str = None,
                proxy: str = None, port: int = None):
    """
    获取或创建浏览器实例。

    连接优先级：
    1. 指定 port → 直接连接该端口（失败则报错，不 fallback）
    2. 已有会话记录的 port → 尝试复用（失败则新建）
    3. 新建浏览器实例
    """
    from DrissionPage import ChromiumPage
    from DrissionPage._configs.chromium_options import ChromiumOptions

    # === 情况1：用户明确指定端口（连接用户自己的浏览器） ===
    if port:
        co = ChromiumOptions(read_file=False)
        co.set_local_port(port)
        co.existing_only(True)
        # 探测浏览器 headless 状态，同步到 options，避免 DrissionPage
        # 因 headless 不匹配执行 quit→restart 导致无头浏览器被关闭
        if _detect_headless(port):
            co.headless(True)
        try:
            page = ChromiumPage(co)
        except Exception as e:
            raise ConnectionError(
                f'无法连接到端口 {port} 的浏览器实例。\n'
                f'请确认浏览器已使用 --remote-debugging-port={port} 启动。\n'
                f'启动命令示例:\n'
                f'  google-chrome --remote-debugging-port={port}\n'
                f'  chromium --remote-debugging-port={port}\n'
                f'原始错误: {e}'
            ) from e
        # 记录到会话（后续命令无需再指定端口）
        save_session(session_name, {
            'port': port,
            'headless': headless,
            'user_data_dir': user_data_dir,
            'user_connected': True,  # 标记：这是用户自己的浏览器
        })
        return page

    # === 情况2：尝试复用已有会话 ===
    sess = load_session(session_name)
    saved_port = sess.get('port')
    probe_dir = sess.get('probe_dir')  # auto-connect 模式记录的 user-data-dir

    # 2a) 先尝试 saved_port 直连
    if saved_port:
        try:
            co = ChromiumOptions(read_file=False)
            co.set_local_port(saved_port)
            co.existing_only(True)
            if _detect_headless(saved_port):
                co.headless(True)
            page = ChromiumPage(co)
            return page
        except Exception:
            pass  # 继续走 2b 或新建

    # 2b) auto-connect 模式：重新发现端口，必要时自动起 bridge
    # 用户重启 Chrome 后端口会变，此路径让 dp 命令无缝继续工作
    if probe_dir:
        auto_connect_flag = sess.get('auto_connect')
        try:
            return _connect_via_bridge(session_name, probe_dir, sess)
        except Exception as e:
            # auto-connect 是用户明确要求连自己的浏览器，不应静默 fallback
            # 到新开浏览器；抛出让上层显示清晰错误
            if auto_connect_flag:
                raise ConnectionError(
                    f'auto-connect 失败: {e}\n'
                    f'probe_dir={probe_dir}\n'
                    f'提示: 如需手动调试，运行:\n'
                    f'  python -m dp_cli.bridge --user-data-dir {probe_dir} --listen 0 -v'
                ) from e

    # 所有复用路径都失败，清会话，落到新建分支
    if saved_port or probe_dir:
        delete_session(session_name)

    # === 情况3：新建浏览器实例 ===
    co = ChromiumOptions()
    if browser_path:
        co.set_browser_path(browser_path)
    if user_data_dir:
        co.set_user_data_path(user_data_dir)
    else:
        # 每个会话有独立的用户数据目录
        uid = get_session_dir() / 'profiles' / session_name
        uid.mkdir(parents=True, exist_ok=True)
        co.set_user_data_path(str(uid))

    if proxy:
        co.set_proxy(proxy)

    if headless:
        co.headless(True)

    co.auto_port(True)

    page = ChromiumPage(co)

    # 保存会话端口
    port = int(page.browser.address.split(':')[-1])
    save_session(session_name, {
        'port': port,
        'headless': headless,
        'user_data_dir': str(co.user_data_path) if co.user_data_path else None,
    })

    return page


# ── Ref 映射管理 ─────────────────────────────────────────────────────────────


def save_refs(session_name: str, url: str, refs: dict) -> None:
    """保存快照编号映射到 refs.json"""
    from datetime import datetime
    data = {
        'url': url,
        'timestamp': datetime.now().isoformat(),
        'refs': refs,
    }
    refs_dir = get_session_dir() / 'refs'
    refs_dir.mkdir(exist_ok=True)
    f = refs_dir / f'{session_name}.json'
    f.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def load_refs(session_name: str) -> dict:
    """加载快照编号映射，返回 {ref_id: {locator, role, name, backendNodeId}}"""
    f = get_session_dir() / 'refs' / f'{session_name}.json'
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding='utf-8'))
        return data.get('refs', {})
    except Exception:
        return {}


def close_browser(session_name: str = 'default', del_data: bool = False) -> bool:
    """关闭指定会话的浏览器。

    - auto-connect / user_connected 模式：只停 bridge 子进程 + 清 session，
      绝不 quit 真 Chrome（浏览器是用户的）。
    - dp 自管模式：调用 page.browser.quit() 彻底结束浏览器进程。
    """
    from DrissionPage import ChromiumPage
    from DrissionPage._configs.chromium_options import ChromiumOptions

    sess = load_session(session_name)
    if not sess:
        return False
    port = sess.get('port')
    bridge_pid = sess.get('bridge_pid')
    user_connected = sess.get('user_connected') or bool(sess.get('probe_dir'))

    # auto-connect / user_connected：不 quit 真 Chrome
    if user_connected:
        if bridge_pid:
            from dp_cli.bridge_manager import stop_bridge
            try:
                stop_bridge(bridge_pid)
            except Exception:
                pass
        delete_session(session_name)
        return True

    if not port:
        delete_session(session_name)
        return False

    try:
        co = ChromiumOptions(read_file=False)
        co.set_local_port(port)
        co.existing_only(True)
        page = ChromiumPage(co)
        page.browser.quit(del_data=del_data)
    except Exception:
        pass

    delete_session(session_name)
    return True
