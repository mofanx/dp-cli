# -*- coding:utf-8 -*-
"""浏览器生命周期命令: open / goto / reload / go-back / go-forward / close / close-all / list / delete-data"""
import click

from dp_cli.session import (get_browser, close_browser, list_sessions,
                            delete_session, load_session, save_session)
from dp_cli.output import ok, error, format_page_info
from dp_cli.commands._utils import session_option, _get_page


def register(cli):

    @cli.command('open')
    @click.argument('url', required=False)
    @session_option
    @click.option('--headless', is_flag=True, help='无头模式')
    @click.option('--browser', 'browser_path', default=None, help='浏览器可执行文件路径')
    @click.option('--profile', 'user_data_dir', default=None, help='用户数据目录')
    @click.option('--proxy', default=None, help='代理服务器，如 http://127.0.0.1:7890')
    @click.option('--port', type=int, default=None, help='连接指定端口的已有浏览器实例')
    @click.option('--new', is_flag=True, help='强制创建新实例（不复用已有会话）')
    def cmd_open(url, session, headless, browser_path, user_data_dir, proxy, port, new):
        """打开浏览器并可选导航到 URL。

        \b
        【复用用户自己的浏览器】(最常见场景，保留登录状态/Cookie/历史)
        第一步：用调试模式启动你自己的 Chrome/Chromium：
          google-chrome --remote-debugging-port=9222
        第二步：用 dp 接管：
          dp open --port 9222
          dp open https://example.com --port 9222
        第三步：后续命令无需再指定 --port（会话自动记住端口）：
          dp snapshot
          dp click "text:登录"

        \b
        【dp 自动管理浏览器】
          dp open
          dp open https://example.com
          dp open https://example.com --headless
          dp -s work open https://github.com
        """
        if new:
            delete_session(session)
        try:
            page = get_browser(session, headless=headless, browser_path=browser_path,
                               user_data_dir=user_data_dir, proxy=proxy, port=port)
        except Exception as e:
            error(f'启动浏览器失败: {e}', code='BROWSER_START_FAILED', detail=str(e))
            return
        if url:
            try:
                page.get(url)
            except Exception as e:
                error(f'导航失败: {e}', code='NAVIGATE_FAILED', detail=str(e))
                return
        ok(format_page_info(page), msg='浏览器已就绪')

    @cli.command()
    @click.argument('url')
    @session_option
    @click.option('--timeout', default=30, help='超时秒数', show_default=True)
    @click.option('--retry', default=3, help='重试次数', show_default=True)
    def goto(url, session, timeout, retry):
        """导航到指定 URL。

        \b
        示例:
          dp goto https://example.com
          dp goto https://example.com --timeout 60
        """
        page = _get_page(session)
        try:
            page.get(url, timeout=timeout, retry=retry)
            ok(format_page_info(page))
        except Exception as e:
            error(f'导航到 {url} 失败', code='NAVIGATE_FAILED', detail=str(e))

    @cli.command()
    @session_option
    def reload(session):
        """刷新当前页面。"""
        page = _get_page(session)
        try:
            page.get(page.url)
            ok(format_page_info(page))
        except Exception as e:
            error(f'刷新失败', code='RELOAD_FAILED', detail=str(e))

    @cli.command('go-back')
    @session_option
    def go_back(session):
        """浏览器后退。"""
        page = _get_page(session)
        try:
            page.back()
            ok(format_page_info(page))
        except Exception as e:
            error('后退失败', code='NAVIGATE_FAILED', detail=str(e))

    @cli.command('go-forward')
    @session_option
    def go_forward(session):
        """浏览器前进。"""
        page = _get_page(session)
        try:
            page.forward()
            ok(format_page_info(page))
        except Exception as e:
            error('前进失败', code='NAVIGATE_FAILED', detail=str(e))

    @cli.command('close')
    @session_option
    @click.option('--del-data', is_flag=True, help='同时删除用户数据目录')
    @click.option('--force', is_flag=True, help='强制关闭浏览器（user_connected 模式下默认只断开连接）')
    def cmd_close(session, del_data, force):
        """关闭浏览器会话。

        如果是通过 --port 连接的用户自己的浏览器，默认只断开连接不关闭浏览器。
        用 --force 才会真正关闭浏览器进程。
        """
        sess = load_session(session)
        if not sess:
            error(f'会话 [{session}] 不存在', code='SESSION_NOT_FOUND')
            return
        user_connected = sess.get('user_connected', False)
        if user_connected and not force:
            delete_session(session)
            ok(msg=f'已断开与 [{session}] 的连接（浏览器仍运行）。用 --force 关闭浏览器。')
        else:
            result = close_browser(session, del_data=del_data)
            if result:
                ok(msg=f'会话 [{session}] 已关闭')
            else:
                error(f'关闭失败', code='CLOSE_FAILED')

    @cli.command('close-all')
    def close_all():
        """关闭所有会话。"""
        sessions = list_sessions()
        closed = []
        for s in sessions:
            close_browser(s['name'])
            closed.append(s['name'])
        ok({'closed': closed}, msg=f'已关闭 {len(closed)} 个会话')

    @cli.command('list')
    def cmd_list():
        """列出所有活跃会话。"""
        sessions = list_sessions()
        ok({'sessions': sessions, 'count': len(sessions)})

    @cli.command('delete-data')
    @session_option
    def delete_data(session):
        """删除会话的用户数据目录。"""
        close_browser(session, del_data=True)
        ok(msg=f'会话 [{session}] 数据已删除')
