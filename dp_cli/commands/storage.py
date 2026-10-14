# -*- coding:utf-8 -*-
"""存储管理命令: cookie-* / localstorage-* / sessionstorage-*"""
import json
from pathlib import Path

import click

from dp_cli.output import ok, error
from dp_cli.commands._utils import (session_option, _get_page,
                                    trim_options, shape_records)

_COOKIE_FIELDS = {'name', 'value', 'domain', 'path', 'secure', 'httpOnly',
                  'sameSite', 'expires'}


def _is_local_or_ip(host: str) -> bool:
    """无点号的合法主机名白名单：localhost 或 IP 字面量（IPv4/IPv6）。"""
    if host == 'localhost':
        return True
    try:
        import ipaddress
        ipaddress.ip_address(host.strip('[]'))
        return True
    except ValueError:
        return False


def register(cli):

    # ── Cookie ──────────────────────────────────

    @cli.command('cookie-list')
    @session_option
    @click.option('--domain', default=None, help='按域名过滤')
    @click.option('--url', default=None, help='按 URL 过滤')
    @trim_options
    def cookie_list(session, domain, url, fields, head):
        """列出所有 Cookie。

        \b
        示例:
          dp cookie-list
          dp cookie-list --domain example.com
        """
        page = _get_page(session)
        try:
            # --domain 先校验：拒绝裸顶级后缀（如 com/org，会导致命中全站 cookie）；
            # localhost / IP 字面量（IPv4/IPv6）是无点号的合法主机名，放行
            fd = None
            if domain:
                fd = domain.lower().strip('.')
                if '.' not in fd and not _is_local_or_ip(fd):
                    error(f'--domain 非法: {domain}（请使用完整域名，如 example.com）',
                          code='INVALID_ARGS')
            # all_info=True 保留 domain/path 等完整字段，供 --domain/--url 过滤
            cookies = page.cookies(all_domains=True, all_info=True)
            # --url：取 host 做 cookie domain 匹配（.example.com 匹配 sub.example.com）
            # urlsplit 正确处理 userinfo/端口/IPv6（[::1]:9222）等形态；
            # 无 scheme 的裸 host 补 '//' 前缀使其被识别为 netloc
            host = None
            if url:
                try:
                    from urllib.parse import urlsplit
                    u = url if '://' in url else '//' + url
                    host = (urlsplit(u).hostname or '').lower() or None
                except ValueError:
                    host = None
            cookie_list_data = []
            for c in cookies:
                c_domain = (c.get('domain') or '')
                if domain:
                    # 域级匹配（标签边界）：example.com 命中 .example.com /
                    # sub.example.com 的 cookie，不命中 notexample.com
                    # （子串匹配会误中）
                    cd = c_domain.lower().strip('.')
                    if not (cd == fd or cd.endswith('.' + fd)
                            or fd.endswith('.' + cd)):
                        continue
                if host:
                    d = c_domain.lstrip('.')
                    if not (host == d or host.endswith('.' + d)):
                        continue
                cookie_list_data.append({'name': c.get('name'),
                                         'value': c.get('value'),
                                         'domain': c_domain,
                                         'path': c.get('path', '/'),
                                         'secure': c.get('secure', False),
                                         'httpOnly': c.get('httpOnly', False),
                                         'sameSite': c.get('sameSite'),
                                         'expires': c.get('expires')})
            shaped, total = shape_records(cookie_list_data, fields, head,
                                          _COOKIE_FIELDS)
            data = {'cookies': shaped, 'count': len(shaped)}
            if head is not None:
                data['total'] = total
            ok(data)
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

    @cli.command('cookie-export')
    @session_option
    @click.option('--domain', default=None, help='只导出指定域名（含子域）的 Cookie')
    @click.option('--format', 'fmt', type=click.Choice(['netscape', 'json']),
                  default='netscape', show_default=True,
                  help='导出格式：netscape 可被 curl/requests/curl_cffi 直接复用')
    @click.option('--output', default=None,
                  help='写入文件路径（默认内联到 data.content）')
    def cookie_export(session, domain, fmt, output):
        """导出 Cookie（默认 Netscape 格式，curl/requests/curl_cffi 可直接加载）。

        典型逆向闭环：浏览器过盾（dp open + stealth）→ 本命令导出
        cf_clearance 等凭据 → dp http-get --impersonate 高速续爬。

        注意：netscape 格式不携带 HttpOnly 标志（该格式无此字段；
        `#HttpOnly_` 前缀是 curl 私有扩展，本导出未启用）。

        \b
        示例:
          dp cookie-export --output cookies.txt
          dp cookie-export --domain example.com --format json
          curl -b cookies.txt https://example.com  # 直接复用
        """
        page = _get_page(session)
        try:
            fd = None
            if domain:
                fd = domain.lower().strip('.')
                if '.' not in fd and not _is_local_or_ip(fd):
                    error(f'--domain 非法: {domain}（请使用完整域名，如 example.com）',
                          code='INVALID_ARGS')
            cookies = page.cookies(all_domains=True, all_info=True)
            items = []
            for c in cookies:
                cd = (c.get('domain') or '').lower().strip('.')
                if fd and not (cd == fd or cd.endswith('.' + fd)
                               or fd.endswith('.' + cd)):
                    continue
                items.append(c)
            if fmt == 'json':
                content = json.dumps(items, ensure_ascii=False, indent=2)
            else:
                lines = ['# Netscape HTTP Cookie File',
                         '# 由 dp-cli 导出，可直接用于 curl -b / requests / curl_cffi']
                for c in items:
                    dom = c.get('domain') or ''
                    secure = 'TRUE' if c.get('secure') else 'FALSE'
                    exp = c.get('expires') or 0
                    try:
                        exp = int(float(exp))
                    except (TypeError, ValueError):
                        exp = 0
                    if exp <= 0:
                        exp = 0  # 会话 cookie → 0
                    lines.append('\t'.join([
                        dom,
                        'TRUE' if dom.startswith('.') else 'FALSE',
                        c.get('path') or '/',
                        secure,
                        str(exp),
                        c.get('name') or '',
                        c.get('value') or '',
                    ]))
                content = '\n'.join(lines) + '\n'
            if output:
                Path(output).write_text(content, encoding='utf-8')
                ok({'saved_to': output, 'count': len(items)})
            else:
                ok({'content': content, 'count': len(items)})
        except Exception as e:
            error(f'导出 Cookie 失败', code='COOKIE_FAILED', detail=str(e))

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

    @cli.command('sessionstorage-delete')
    @click.argument('key')
    @session_option
    def sessionstorage_delete(key, session):
        """删除 sessionStorage 指定键。"""
        page = _get_page(session)
        try:
            page.run_js(f'sessionStorage.removeItem({json.dumps(key)})', as_expr=True)
            ok({'key': key}, msg='sessionStorage 键已删除')
        except Exception as e:
            error(f'删除 sessionStorage 失败', code='STORAGE_FAILED', detail=str(e))

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
