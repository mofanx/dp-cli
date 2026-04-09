# -*- coding:utf-8 -*-
"""杂项命令: resize / maximize / state-save / state-load / config-set"""
import json
from pathlib import Path

import click

from dp_cli.output import ok, error
from dp_cli.commands._utils import session_option, _get_page


def register(cli):

    @cli.command('resize')
    @click.argument('width', type=int)
    @click.argument('height', type=int)
    @session_option
    def resize(width, height, session):
        """调整浏览器窗口大小。

        \b
        示例:
          dp resize 1920 1080
          dp resize 375 812
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
    @session_option
    def state_save(filename, session):
        """保存浏览器状态（Cookie + localStorage）到文件。

        \b
        示例:
          dp state-save
          dp state-save auth.json
        """
        page = _get_page(session)
        try:
            state = {
                'cookies': page.cookies(all_domains=True).as_dict(),
                'url': page.url,
            }
            try:
                ls = page.run_js(
                    'return JSON.stringify(Object.fromEntries(Object.entries(localStorage)))',
                    as_expr=True)
                state['localStorage'] = json.loads(ls) if isinstance(ls, str) else ls or {}
            except Exception:
                state['localStorage'] = {}
            Path(filename).write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
            ok({'filename': str(Path(filename).absolute()),
                'cookies_count': len(state['cookies'])}, msg='状态已保存')
        except Exception as e:
            error(f'保存状态失败', code='STATE_FAILED', detail=str(e))

    @cli.command('state-load')
    @click.argument('filename', default='state.json')
    @session_option
    def state_load(filename, session):
        """从文件加载浏览器状态（Cookie + localStorage）。

        \b
        示例:
          dp state-load
          dp state-load auth.json
        """
        page = _get_page(session)
        try:
            state = json.loads(Path(filename).read_text(encoding='utf-8'))
            if 'cookies' in state:
                for name, value in state['cookies'].items():
                    try:
                        page.set.cookies({'name': name, 'value': value})
                    except Exception:
                        pass
            if 'localStorage' in state and state['localStorage']:
                for k, v in state['localStorage'].items():
                    try:
                        page.run_js(f'localStorage.setItem({json.dumps(k)}, {json.dumps(v)})',
                                    as_expr=True)
                    except Exception:
                        pass
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
          dp config-set --browser-path /usr/bin/google-chrome
          dp config-set --user-path /home/user/.chrome-data
          dp config-set --copy-config
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
