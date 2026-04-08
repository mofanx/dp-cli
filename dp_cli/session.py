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
    if saved_port:
        try:
            co = ChromiumOptions(read_file=False)
            co.set_local_port(saved_port)
            co.existing_only(True)
            page = ChromiumPage(co)
            return page
        except Exception:
            # 会话失效，删除记录，继续新建
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


def close_browser(session_name: str = 'default', del_data: bool = False) -> bool:
    """关闭指定会话的浏览器"""
    from DrissionPage import ChromiumPage
    from DrissionPage._configs.chromium_options import ChromiumOptions

    sess = load_session(session_name)
    port = sess.get('port')
    if not port:
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
