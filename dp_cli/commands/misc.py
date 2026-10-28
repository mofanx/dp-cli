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
    seen = set()
    _LS_JS = ('JSON.stringify({o:location.origin,'
              'items:Object.entries(localStorage)'
              '.map(function(p){return {name:p[0],value:p[1]}})})')

    def _collect(target):
        """采单个 frame 的 localStorage 并入 origins（按 origin 去重）。"""
        try:
            raw = target.run_js(_LS_JS, as_expr=True)
            data = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(data, dict):
                return
            origin = data.get('o') or ''
            items = data.get('items') or []
            u = urlparse(origin)
            if u.scheme not in ('http', 'https') or origin in seen:
                return
            seen.add(origin)
            if items:
                origins.append({
                    'origin': origin,
                    'localStorage': [
                        {'name': str(i.get('name', '')),
                         'value': str(i.get('value', ''))}
                        for i in items if isinstance(i, dict)]})
        except Exception:
            pass

    def _walk(target, depth=0):
        if depth > 4:
            return
        _collect(target)
        try:
            for fe in target.eles('tag:iframe'):
                try:
                    fr = getattr(fe, 'frame', None)
                    if callable(fr):
                        fr = fr()
                except Exception:
                    continue
                if fr is not None:
                    _walk(fr, depth + 1)
        except Exception:
            pass

    try:
        _walk(page)
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
            # tmp+os.replace 原子产出——写一半中断不留截断的 state 文件
            # （同 cookie-export --output 模式）
            import os as _os
            _p = Path(filename)
            _tmp = _p.with_name(_p.name + '.tmp')
            _tmp.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
            _os.replace(_tmp, _p)
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
            cookies = state.get('cookies')
            n_cookies = 0
            if isinstance(cookies, list):
                # Playwright storageState：cookies 为对象数组（全字段恢复）
                same_site_map = {'Lax': 'Lax', 'Strict': 'Strict',
                                 'None': 'None', 'no_restriction': 'None'}
                for c in cookies:
                    try:
                        ck = {'name': c['name'], 'value': c['value']}
                        for f in ('domain', 'path', 'secure', 'httpOnly', 'expires'):
                            if c.get(f) is not None:
                                ck[f] = c[f]
                        if c.get('sameSite') in same_site_map:
                            ck['sameSite'] = same_site_map[c['sameSite']]
                        page.set.cookies(ck)
                        n_cookies += 1
                    except Exception as e:
                        debug(f'恢复 cookie {c.get("name")} 失败', e)
            elif isinstance(cookies, dict):
                for name, value in cookies.items():
                    try:
                        page.set.cookies({'name': name, 'value': value})
                        n_cookies += 1
                    except Exception as e:
                        debug(f'恢复 cookie {name} 失败', e)
            # localStorage：dp 格式顶层 dict；pw 格式 origins[].localStorage
            ls_map = {}
            if isinstance(state.get('localStorage'), dict):
                ls_map.update(state['localStorage'])
            for origin in (state.get('origins') or []):
                for item in (origin.get('localStorage') or []):
                    ls_map[item['name']] = item['value']
            for k, v in ls_map.items():
                try:
                    page.run_js(f'localStorage.setItem({json.dumps(k)}, {json.dumps(v)})',
                                as_expr=True)
                except Exception as e:
                    debug(f'恢复 localStorage[{k}] 失败', e)
            ok({'filename': filename,
                'cookies_restored': n_cookies,
                'localStorage_restored': len(ls_map)}, msg='状态已加载')
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
