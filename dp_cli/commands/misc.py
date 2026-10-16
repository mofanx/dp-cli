# -*- coding:utf-8 -*-
"""杂项命令: resize / maximize / state-save / state-load / config-set"""
import json
from pathlib import Path
from urllib.parse import urlparse

import click

from dp_cli.output import ok, error, debug
from dp_cli.commands._utils import session_option, _get_page

_PW_SAME_SITE = {'strict': 'Strict', 'lax': 'Lax', 'none': 'None',
                 'no_restriction': 'None'}


def _pw_same_site(v):
    """CDP sameSite → Playwright 枚举；缺失按现代浏览器默认 Lax。"""
    return _PW_SAME_SITE.get(str(v or '').strip().lower(), 'Lax')


def _pw_expires(c):
    """会话 cookie（session 标志或 expires<=0/缺失）→ -1。"""
    if c.get('session'):
        return -1
    try:
        exp = float(c.get('expires'))
    except (TypeError, ValueError):
        return -1
    return exp if exp > 0 else -1


def _playwright_storage_state(page):
    """采集 cookie + localStorage，输出 Playwright storageState 结构，
    可直接 browser.new_context(storage_state=...)。"""
    cookies = []
    for c in page.cookies(all_domains=True, all_info=True) or []:
        cookies.append({
            'name': str(c.get('name') or ''),
            'value': str(c.get('value') or ''),
            'domain': str(c.get('domain') or ''),
            'path': str(c.get('path') or '/'),
            'expires': _pw_expires(c),
            'httpOnly': bool(c.get('httpOnly')),
            'secure': bool(c.get('secure')),
            'sameSite': _pw_same_site(c.get('sameSite')),
        })
    origins = []
    try:
        u = urlparse(page.url or '')
        if u.scheme in ('http', 'https'):
            ls = page.run_js(
                'JSON.stringify(Object.entries(localStorage)'
                '.map(function(p){return {name:p[0],value:p[1]}}))',
                as_expr=True)
            items = json.loads(ls) if isinstance(ls, str) else ls
            if items:
                origins.append({
                    'origin': f'{u.scheme}://{u.netloc}',
                    'localStorage': [
                        {'name': str(i.get('name', '')),
                         'value': str(i.get('value', ''))}
                        for i in items if isinstance(i, dict)]})
    except Exception:
        pass
    return {'cookies': cookies, 'origins': origins}


def register(cli):

    @cli.command('resize')
    @click.argument('width', type=int)
    @click.argument('height', type=int)
    @session_option
    def resize(width, height, session):
        """调整浏览器窗口大小。

        \b
        示例:
          dpx resize 1920 1080
          dpx resize 375 812
        """
        page = _get_page(session)
        try:
            page.set.window.size(width, height)
            ok({'width': width, 'height': height}, msg='窗口大小已调整')
        except Exception as e:
            error(f'调整窗口大小失败', code='RESIZE_FAILED', detail=str(e))

    @cli.command('maximize')
    @session_option
    def maximize(session):
        """最大化浏览器窗口。"""
        page = _get_page(session)
        try:
            page.set.window.max()
            ok(msg='窗口已最大化')
        except Exception as e:
            error(f'最大化失败', code='WINDOW_FAILED', detail=str(e))

    @cli.command('state-save')
    @click.argument('filename', default='state.json')
    @click.option('--format', 'fmt',
                  type=click.Choice(['dp', 'playwright']), default='dp',
                  show_default=True,
                  help='输出格式：playwright 为 Playwright storageState 标准 '
                       'JSON，可直接 new_context(storage_state=...)')
    @session_option
    def state_save(filename, fmt, session):
        """保存浏览器状态（Cookie + localStorage）到文件。

        \b
        示例:
          dpx state-save
          dpx state-save auth.json
          dpx state-save auth.json --format playwright  # 供 Playwright/pwtest 复用登录态
        """
        page = _get_page(session)
        try:
            if fmt == 'playwright':
                state = _playwright_storage_state(page)
            else:
                state = {
                    'cookies': page.cookies(all_domains=True).as_dict(),
                    'url': page.url,
                }
                try:
                    ls = page.run_js(
                        'JSON.stringify(Object.fromEntries(Object.entries(localStorage)))',
                        as_expr=True)
                    state['localStorage'] = json.loads(ls) if isinstance(ls, str) else ls or {}
                except Exception:
                    state['localStorage'] = {}
            Path(filename).write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
            ok({'filename': str(Path(filename).absolute()),
                'cookies_count': len(state['cookies']), 'format': fmt},
               msg='状态已保存')
        except Exception as e:
            error(f'保存状态失败', code='STATE_FAILED', detail=str(e))

    @cli.command('state-load')
    @click.argument('filename', default='state.json')
    @session_option
    def state_load(filename, session):
        """从文件加载浏览器状态（Cookie + localStorage）。

        \b
        示例:
          dpx state-load
          dpx state-load auth.json
        """
        page = _get_page(session)
        try:
            state = json.loads(Path(filename).read_text(encoding='utf-8'))
            if 'cookies' in state:
                for name, value in state['cookies'].items():
                    try:
                        page.set.cookies({'name': name, 'value': value})
                    except Exception as e:
                        debug(f'恢复 cookie {name} 失败', e)
            if 'localStorage' in state and state['localStorage']:
                for k, v in state['localStorage'].items():
                    try:
                        page.run_js(f'localStorage.setItem({json.dumps(k)}, {json.dumps(v)})',
                                    as_expr=True)
                    except Exception as e:
                        debug(f'恢复 localStorage[{k}] 失败', e)
            ok({'filename': filename,
                'cookies_restored': len(state.get('cookies', {}))}, msg='状态已加载')
        except FileNotFoundError:
            error(f'状态文件不存在: {filename}', code='FILE_NOT_FOUND')
        except Exception as e:
            error(f'加载状态失败', code='STATE_FAILED', detail=str(e))

    @cli.command('config-set')
    @click.option('-p', '--browser-path', default=None, help='设置浏览器路径')
    @click.option('-u', '--user-path', default=None, help='设置用户数据路径')
    @click.option('-c', '--copy-config', is_flag=True, help='复制默认配置文件到当前目录')
    def config_set(browser_path, user_path, copy_config):
        """修改 DrissionPage 配置文件。

        \b
        示例:
          dpx config-set --browser-path /usr/bin/google-chrome
          dpx config-set --user-path /home/user/.chrome-data
          dpx config-set --copy-config
        """
        from DrissionPage._configs.chromium_options import ChromiumOptions
        from DrissionPage._functions.tools import configs_to_here

        if copy_config:
            configs_to_here()
            ok(msg='配置文件已复制到当前目录')

        if browser_path or user_path:
            co = ChromiumOptions()
            if browser_path:
                co.set_browser_path(browser_path)
            if user_path:
                co.set_user_data_path(user_path)
            co.save()
            ok(msg='配置已保存')
        elif not copy_config:
            click.echo('请至少指定一个选项。使用 --help 查看可用参数。')
