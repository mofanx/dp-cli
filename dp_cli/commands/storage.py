# -*- coding:utf-8 -*-
"""存储管理命令: cookie-* / localstorage-* / sessionstorage-*"""
import json

import click

from dp_cli.output import ok, error
from dp_cli.commands._utils import session_option, _get_page


def register(cli):

    # ── Cookie ──────────────────────────────────

    @cli.command('cookie-list')
    @session_option
    @click.option('--domain', default=None, help='按域名过滤')
    @click.option('--url', default=None, help='按 URL 过滤')
    def cookie_list(session, domain, url):
        """列出所有 Cookie。

        \b
        示例:
          dp cookie-list
          dp cookie-list --domain example.com
        """
        page = _get_page(session)
        try:
            cookies = page.cookies(all_domains=True).as_dict()
            cookie_list_data = []
            for name, value in cookies.items():
                cookie_list_data.append({'name': name, 'value': value})
            ok({'cookies': cookie_list_data, 'count': len(cookie_list_data)})
        except Exception as e:
            error(f'获取 Cookie 失败', code='COOKIE_FAILED', detail=str(e))

    @cli.command('cookie-get')
    @click.argument('name')
    @session_option
    def cookie_get(name, session):
        """获取指定名称的 Cookie 值。"""
        page = _get_page(session)
        try:
            cookies = page.cookies().as_dict()
            value = cookies.get(name)
            if value is None:
                error(f'Cookie 不存在: {name}', code='COOKIE_NOT_FOUND')
                return
            ok({'name': name, 'value': value})
        except Exception as e:
            error(f'获取 Cookie 失败', code='COOKIE_FAILED', detail=str(e))

    @cli.command('cookie-set')
    @click.argument('name')
    @click.argument('value')
    @session_option
    @click.option('--domain', default=None, help='Cookie 域名')
    @click.option('--path', default='/', help='Cookie 路径', show_default=True)
    @click.option('--http-only', is_flag=True, help='设置 HttpOnly')
    @click.option('--secure', is_flag=True, help='设置 Secure')
    def cookie_set(name, value, session, domain, path, http_only, secure):
        """设置 Cookie。

        \b
        示例:
          dp cookie-set session_id abc123
          dp cookie-set token xxx --domain example.com --http-only --secure
        """
        page = _get_page(session)
        try:
            kwargs = {'name': name, 'value': value, 'path': path}
            if domain:
                kwargs['domain'] = domain
            if http_only:
                kwargs['httpOnly'] = True
            if secure:
                kwargs['secure'] = True
            page.set.cookies(kwargs)
            ok({'name': name, 'value': value}, msg='Cookie 已设置')
        except Exception as e:
            error(f'设置 Cookie 失败', code='COOKIE_FAILED', detail=str(e))

    @cli.command('cookie-delete')
    @click.argument('name')
    @session_option
    def cookie_delete(name, session):
        """删除指定 Cookie。"""
        page = _get_page(session)
        try:
            page.run_cdp('Network.deleteCookies', name=name)
            ok({'name': name}, msg='Cookie 已删除')
        except Exception as e:
            error(f'删除 Cookie 失败', code='COOKIE_FAILED', detail=str(e))

    @cli.command('cookie-clear')
    @session_option
    def cookie_clear(session):
        """清除所有 Cookie。"""
        page = _get_page(session)
        try:
            page.run_cdp('Network.clearBrowserCookies')
            ok(msg='所有 Cookie 已清除')
        except Exception as e:
            error(f'清除 Cookie 失败', code='COOKIE_FAILED', detail=str(e))

    # ── LocalStorage ────────────────────────────

    @cli.command('localstorage-list')
    @session_option
    def localstorage_list(session):
        """列出所有 localStorage 条目。"""
        page = _get_page(session)
        try:
            result = page.run_js(
                'return JSON.stringify(Object.fromEntries(Object.entries(localStorage)))',
                as_expr=True)
            data = json.loads(result) if isinstance(result, str) else result or {}
            ok({'storage': data, 'count': len(data)})
        except Exception as e:
            error(f'获取 localStorage 失败', code='STORAGE_FAILED', detail=str(e))

    @cli.command('localstorage-get')
    @click.argument('key')
    @session_option
    def localstorage_get(key, session):
        """获取 localStorage 指定键的值。"""
        page = _get_page(session)
        try:
            value = page.local_storage(key)
            ok({'key': key, 'value': value})
        except Exception as e:
            error(f'获取 localStorage 失败', code='STORAGE_FAILED', detail=str(e))

    @cli.command('localstorage-set')
    @click.argument('key')
    @click.argument('value')
    @session_option
    def localstorage_set(key, value, session):
        """设置 localStorage 键值。"""
        page = _get_page(session)
        try:
            page.run_js(f'localStorage.setItem({json.dumps(key)}, {json.dumps(value)})',
                        as_expr=True)
            ok({'key': key, 'value': value}, msg='localStorage 已设置')
        except Exception as e:
            error(f'设置 localStorage 失败', code='STORAGE_FAILED', detail=str(e))

    @cli.command('localstorage-delete')
    @click.argument('key')
    @session_option
    def localstorage_delete(key, session):
        """删除 localStorage 指定键。"""
        page = _get_page(session)
        try:
            page.run_js(f'localStorage.removeItem({json.dumps(key)})', as_expr=True)
            ok({'key': key}, msg='localStorage 键已删除')
        except Exception as e:
            error(f'删除 localStorage 失败', code='STORAGE_FAILED', detail=str(e))

    @cli.command('localstorage-clear')
    @session_option
    def localstorage_clear(session):
        """清除所有 localStorage。"""
        page = _get_page(session)
        try:
            page.run_js('localStorage.clear()', as_expr=True)
            ok(msg='localStorage 已清除')
        except Exception as e:
            error(f'清除 localStorage 失败', code='STORAGE_FAILED', detail=str(e))

    # ── SessionStorage ───────────────────────────

    @cli.command('sessionstorage-list')
    @session_option
    def sessionstorage_list(session):
        """列出所有 sessionStorage 条目。"""
        page = _get_page(session)
        try:
            result = page.run_js(
                'JSON.stringify(Object.fromEntries(Object.entries(sessionStorage)))',
                as_expr=True)
            data = json.loads(result) if isinstance(result, str) else result or {}
            ok({'storage': data, 'count': len(data)})
        except Exception as e:
            error(f'获取 sessionStorage 失败', code='STORAGE_FAILED', detail=str(e))

    @cli.command('sessionstorage-get')
    @click.argument('key')
    @session_option
    def sessionstorage_get(key, session):
        """获取 sessionStorage 指定键的值。"""
        page = _get_page(session)
        try:
            value = page.session_storage(key)
            ok({'key': key, 'value': value})
        except Exception as e:
            error(f'获取 sessionStorage 失败', code='STORAGE_FAILED', detail=str(e))

    @cli.command('sessionstorage-set')
    @click.argument('key')
    @click.argument('value')
    @session_option
    def sessionstorage_set(key, value, session):
        """设置 sessionStorage 键值。"""
        page = _get_page(session)
        try:
            page.run_js(f'sessionStorage.setItem({json.dumps(key)}, {json.dumps(value)})',
                        as_expr=True)
            ok({'key': key, 'value': value}, msg='sessionStorage 已设置')
        except Exception as e:
            error(f'设置 sessionStorage 失败', code='STORAGE_FAILED', detail=str(e))

    @cli.command('sessionstorage-clear')
    @session_option
    def sessionstorage_clear(session):
        """清除所有 sessionStorage。"""
        page = _get_page(session)
        try:
            page.run_js('sessionStorage.clear()', as_expr=True)
            ok(msg='sessionStorage 已清除')
        except Exception as e:
            error(f'清除 sessionStorage 失败', code='STORAGE_FAILED', detail=str(e))
