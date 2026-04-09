# -*- coding:utf-8 -*-
"""网络命令: listen / listen-stop / http-get / http-post"""
import json
from pathlib import Path

import click

from dp_cli.output import ok, error
from dp_cli.commands._utils import session_option, _get_page


def register(cli):

    @cli.command('listen')
    @session_option
    @click.option('--filter', 'url_filter', default=None, help='URL 过滤关键字，如 "api/user"')
    @click.option('--count', default=10, help='最多捕获请求数', show_default=True)
    @click.option('--timeout', default=30, help='监听超时秒数', show_default=True)
    @click.option('--method', default=None, help='过滤请求方法，如 POST')
    def listen(session, url_filter, count, timeout, method):
        """监听网络请求（抓包）。先 listen，再执行触发操作，最后 listen-stop 读取结果。

        \b
        这是 DrissionPage 的核心独特能力，可精确捕获 XHR/Fetch/图片等任意请求。

        \b
        示例:
          dp listen --filter "api/login"
          dp listen --count 5 --timeout 10
        """
        page = _get_page(session)
        try:
            targets = url_filter if url_filter else None
            page.listen.start(targets=targets, method=method)
            ok(msg=f'已开始监听，过滤: {url_filter or "全部"}')
        except Exception as e:
            error(f'启动监听失败', code='LISTEN_FAILED', detail=str(e))

    @cli.command('listen-stop')
    @session_option
    @click.option('--count', default=1, help='等待数据包数量', show_default=True)
    @click.option('--timeout', default=10, help='等待数据超时秒数', show_default=True)
    def listen_stop(session, count, timeout):
        """停止监听并获取捕获的网络请求数据。"""
        page = _get_page(session)
        try:
            packets = page.listen.wait(count=count, timeout=timeout, fit_count=False)
            results = []
            if packets:
                pkts = packets if isinstance(packets, list) else [packets]
                for pkt in pkts:
                    try:
                        item = {
                            'url': pkt.url,
                            'method': pkt.method,
                            'status': pkt.response.status if pkt.response else None,
                            'type': pkt.resourceType,
                        }
                        try:
                            item['response_body'] = pkt.response.body
                        except Exception:
                            pass
                        results.append(item)
                    except Exception:
                        continue
            page.listen.stop()
            ok({'packets': results, 'count': len(results)})
        except Exception as e:
            error(f'获取监听数据失败', code='LISTEN_FAILED', detail=str(e))

    @cli.command('http-get')
    @click.argument('url')
    @click.option('--headers', default=None, help='JSON 格式的 Headers')
    @click.option('--proxy', default=None, help='代理地址')
    @click.option('--timeout', default=30, help='超时秒数', show_default=True)
    @click.option('--output', default=None, help='响应体保存路径')
    def http_get(url, headers, proxy, timeout, output):
        """发送 HTTP GET 请求（不启动浏览器，高效爬虫模式）。

        \b
        示例:
          dp http-get https://api.example.com/users
          dp http-get https://example.com --output page.html
          dp http-get https://api.example.com --headers '{"Authorization":"Bearer xxx"}'
        """
        try:
            from DrissionPage import SessionPage
            from DrissionPage._configs.session_options import SessionOptions

            so = SessionOptions()
            if proxy:
                so.set_proxies(http=proxy, https=proxy)
            if headers:
                so.set_headers(json.loads(headers))

            page = SessionPage(session_or_options=so)
            page.get(url, timeout=timeout)

            result = {
                'url': page.url,
                'status_code': page.response.status_code if page.response else None,
            }
            try:
                result['body'] = page.response.json()
            except Exception:
                try:
                    body_text = page.response.text[:3000]
                except Exception:
                    body_text = '<binary>'
                if output:
                    Path(output).write_text(page.response.text, encoding='utf-8')
                    result['saved_to'] = output
                else:
                    result['body'] = body_text

            ok(result)
        except Exception as e:
            error(f'HTTP GET 失败', code='HTTP_FAILED', detail=str(e))

    @cli.command('http-post')
    @click.argument('url')
    @click.option('--data', default=None, help='JSON 格式的请求体')
    @click.option('--form', default=None, help='JSON 格式的表单数据')
    @click.option('--headers', default=None, help='JSON 格式的 Headers')
    @click.option('--proxy', default=None, help='代理地址')
    @click.option('--timeout', default=30, help='超时秒数', show_default=True)
    def http_post(url, data, form, headers, proxy, timeout):
        """发送 HTTP POST 请求（不启动浏览器）。

        \b
        示例:
          dp http-post https://api.example.com/login --data '{"user":"admin","pass":"123"}'
          dp http-post https://example.com/form --form '{"field":"value"}'
        """
        try:
            from DrissionPage import SessionPage
            from DrissionPage._configs.session_options import SessionOptions

            so = SessionOptions()
            if proxy:
                so.set_proxies(http=proxy, https=proxy)
            if headers:
                so.set_headers(json.loads(headers))

            page = SessionPage(session_or_options=so)

            kwargs = {'timeout': timeout}
            if data:
                kwargs['json'] = json.loads(data)
            elif form:
                kwargs['data'] = json.loads(form)

            page.post(url, **kwargs)

            result = {
                'url': page.url,
                'status_code': page.response.status_code if page.response else None,
            }
            try:
                result['body'] = page.response.json()
            except Exception:
                try:
                    result['body'] = page.response.text[:3000]
                except Exception:
                    result['body'] = '<binary>'

            ok(result)
        except Exception as e:
            error(f'HTTP POST 失败', code='HTTP_FAILED', detail=str(e))
