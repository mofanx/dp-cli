# -*- coding:utf-8 -*-
"""网络命令: listen / listen-stop / http-get / http-post / har-dump"""
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter, sleep as _sleep

import click

from dp_cli.output import ok, error, debug
from dp_cli.commands._utils import (session_option, _get_page,
                                    trim_options, shape_records)

_PACKET_FIELDS = {'url', 'method', 'status', 'type', 'request_headers',
                  'postData', 'response_headers', 'response_body'}


def _requests_no_buffer(session):
    error('无被动网络日志（需要常驻进程托管事件缓冲；'
          '或使用 dpx listen 主动监听）', code='NO_NETLOG',
          detail=f'dpx serve --session {session} 或 '
                 f'dpx daemon-start -S {session} + DPX_DAEMON=1')


def _register_requests(cli):
    @cli.command('requests')
    @session_option
    @click.option('--filter', 'furl', default=None,
                  help='URL 子串过滤（如 "api/"）')
    @click.option('--method', 'fmethod', default=None,
                  help='HTTP 方法过滤（GET/POST/...）')
    @click.option('--type', 'ftype', default=None,
                  help='资源类型过滤（XHR/Fetch/Document/...）')
    @click.option('--failed', 'ffailed', is_flag=True,
                  help='只看失败请求')
    @click.option('--head', type=int, default=None,
                  help='只输出前 N 条（按时间序）')
    def cmd_requests(session, furl, fmethod, ftype, ffailed, head):
        """列出被动网络日志（serve 进程托管的 ~500 条 ring buffer）。

        需先跑 `dpx serve --session <name>`——常驻进程替你开着
        Network.enable 监听滚动写盘；无缓冲时提示改用 dpx listen。

        \b
        示例:
          dpx requests                       # 全部（时间序）
          dpx requests --filter api/         # URL 过滤
          dpx requests --method POST --failed
        """
        from dp_cli.netlog import read_entries
        entries = read_entries(session)
        if entries is None:
            _requests_no_buffer(session)
            return
        # 按 requestId 聚合 req+resp 成行
        reqs = {}
        for e in entries:
            rid = e.get('id')
            if rid is None:
                continue
            slot = reqs.setdefault(rid, {})
            slot.update({k: v for k, v in e.items() if v is not None})
            if e['kind'] == 'resp':
                slot['status'] = e.get('status')
            if e['kind'] == 'fail':
                slot['failed'] = e.get('errorText') or 'failed'
        rows = list(reqs.values())
        if furl:
            rows = [r for r in rows if furl in (r.get('url') or '')]
        if fmethod:
            rows = [r for r in rows
                    if (r.get('method') or '').upper() == fmethod.upper()]
        if ftype:
            rows = [r for r in rows
                    if (r.get('type') or '').lower() == ftype.lower()]
        if ffailed:
            rows = [r for r in rows if r.get('failed')]
        if head:
            rows = rows[:head]
        slim = [{'id': r.get('id'), 'method': r.get('method'),
                 'url': r.get('url'), 'status': r.get('status'),
                 'type': r.get('type'),
                 **({'failed': r['failed']} if r.get('failed') else {})}
                for r in rows]
        ok({'count': len(slim), 'requests': slim})

    @cli.command('request')
    @session_option
    @click.argument('request_id')
    @click.option('--body', is_flag=True, help='尝试拉响应体（CDP）')
    def cmd_request(session, request_id, body):
        """查看某请求的 headers/body 详情（按 requests 列表里的 id）。

        \b
        示例:
          dpx request 42.17
          dpx request 42.17 --body
        """
        from dp_cli.netlog import read_entries
        entries = read_entries(session)
        if entries is None:
            _requests_no_buffer(session)
            return
        matched = [e for e in entries if e.get('id') == request_id]
        if not matched:
            error(f'请求 id 不存在: {request_id}', code='REQUEST_NOT_FOUND')
            return
        merged = {}
        for e in matched:
            merged.update({k: v for k, v in e.items() if v is not None})
        if body:
            try:
                page = _get_page(session)
                r = page.run_cdp('Network.getResponseBody',
                                 requestId=request_id)
                merged['body'] = r.get('body')
                merged['body_base64'] = r.get('base64Encoded', False)
            except Exception as e:
                merged['body_error'] = str(e)
        ok(merged)


def _emit_response(url, resp, output):
    """构造 http-get/http-post 的输出。

    :param url: 最终 URL（重定向后与请求 URL 可能不同）
    :param resp: requests/curl_cffi Response，可为 None

    --output 指定时把原始响应字节写入文件（兼容二进制），
    否则把 body 内联到结果（JSON 优先，文本截断 3000 字）。
    """
    result = {
        'url': url,
        'status_code': resp.status_code if resp is not None else None,
    }
    if output:
        # response 为 None（请求失败未拿到响应）时写空文件，避免 AttributeError
        Path(output).write_bytes(resp.content if resp is not None else b'')
        result['saved_to'] = output
        if resp is None:
            result['warning'] = '无响应内容（response is None），已写入空文件'
        return result
    if resp is None:
        result['body'] = None
        return result
    try:
        result['body'] = resp.json()
    except Exception:
        try:
            result['body'] = resp.text[:3000]
        except Exception:
            result['body'] = '<binary>'
    return result


def _cffi_request(url, method, headers, proxy, timeout, impersonate,
                  data=None, form=None):
    """curl_cffi 后端：模拟浏览器 TLS/JA3/HTTP2 指纹发请求。

    :return: (final_url, response)；curl_cffi 缺失时抛 ImportError
    """
    from curl_cffi import requests as cffi
    kwargs = {'headers': headers, 'timeout': timeout,
              'impersonate': impersonate or 'chrome'}
    if proxy:
        kwargs['proxies'] = {'http': proxy, 'https': proxy}
    if method == 'POST':
        if data is not None:
            kwargs['json'] = data
        elif form is not None:
            kwargs['data'] = form
    resp = getattr(cffi, method.lower())(url, **kwargs)
    return str(resp.url), resp


def _requests_request(url, method, headers, proxy, timeout,
                      data=None, form=None):
    """DrissionPage SessionPage（requests）后端。"""
    from DrissionPage import SessionPage
    from DrissionPage._configs.session_options import SessionOptions

    so = SessionOptions()
    if proxy:
        so.set_proxies(http=proxy, https=proxy)
    if headers:
        so.set_headers(headers)
    page = SessionPage(session_or_options=so)
    kwargs = {'timeout': timeout}
    if method == 'POST':
        if data is not None:
            kwargs['json'] = data
        elif form is not None:
            kwargs['data'] = form
    getattr(page, method.lower())(url, **kwargs)
    return page.url, page.response


def _http_send(url, method, headers, proxy, timeout, impersonate,
               data=None, form=None):
    """http-get/http-post 统一发送。--impersonate 指定时优先 curl_cffi
    （真实浏览器 TLS 指纹）；缺包则降级 requests 后端并附 warning。

    :return: (result_url, response, warnings)
    """
    warnings = []
    if impersonate:
        try:
            final_url, resp = _cffi_request(
                url, method, headers, proxy, timeout, impersonate,
                data=data, form=form)
            return final_url, resp, warnings
        except ImportError:
            warnings.append(
                'curl_cffi 未安装，--impersonate 已降级为 requests 后端'
                '（TLS 指纹为 Python 默认值）。安装: pip install dp-cli[tls]')
            debug('curl_cffi 不可用，降级 requests 后端')
    final_url, resp = _requests_request(
        url, method, headers, proxy, timeout, data=data, form=form)
    return final_url, resp, warnings


def _pkg_version() -> str:
    """包版本（HAR creator 用）；取不到时回退 'unknown'。"""
    try:
        from importlib.metadata import version
        return version('dp-cli')
    except Exception:
        return 'unknown'


def _hdr_list(headers) -> list:
    """CDP headers dict → HAR [{name, value}] 列表。"""
    if not isinstance(headers, dict):
        return []
    return [{'name': k, 'value': str(v)} for k, v in headers.items()]


def _iso_ts(wall_time) -> str:
    """CDP wallTime（unix 秒，可为浮点）→ ISO8601 UTC。"""
    try:
        return datetime.fromtimestamp(float(wall_time), tz=timezone.utc) \
            .isoformat().replace('+00:00', 'Z')
    except Exception:
        return ''


def _build_har(entries: dict, page_url: str = '') -> dict:
    """把收集的 Network 事件 dict 聚合为 HAR 1.2。"""
    out_entries = []
    for rid, e in entries.items():
        timing = e.pop('_timing', None) or {}
        resp_ts = e.pop('_resp_ts', None)
        end_ts = e.pop('_end_ts', None)
        e.pop('_done', None)
        e.pop('_failed', None)
        send = max(0.0, timing.get('sendEnd', 0) - timing.get('sendStart', 0))
        wait = max(0.0, timing.get('receiveHeadersEnd', 0)
                   - timing.get('sendEnd', 0))
        receive = (max(0.0, (end_ts - resp_ts) * 1000)
                   if (resp_ts and end_ts) else 0)
        e['timings'] = {'send': send, 'wait': wait, 'receive': receive}
        e['time'] = send + wait + receive
        out_entries.append(e)
    out_entries.sort(key=lambda x: x.get('startedDateTime', ''))
    har = {'log': {
        'version': '1.2',
        'creator': {'name': 'dp-cli', 'version': _pkg_version()},
        'browser': {'name': 'chromium'},
        'pages': [],
        'entries': out_entries,
    }}
    if page_url:
        har['log']['pages'] = [{
            'id': 'page_1', 'title': page_url,
            'startedDateTime': out_entries[0]['startedDateTime']
            if out_entries else '',
            'pageTimings': {},
        }]
    return har


def register(cli):
    _register_requests(cli)

    @cli.command('listen')
    @session_option
    @click.option('--filter', 'url_filter', default=None, help='URL 过滤关键字，如 "api/user"')
    # --count/--timeout 对 listen.start 无意义（作用于 listen-stop 的 wait），
    # 保留参数兼容性但从 help 隐藏，避免误导
    @click.option('--count', default=10, hidden=True)
    @click.option('--timeout', default=30, hidden=True)
    @click.option('--method', default=None, help='过滤请求方法，如 POST')
    def listen(session, url_filter, count, timeout, method):
        """监听网络请求（抓包）。先 listen，再执行触发操作，最后 listen-stop 读取结果。

        \b
        这是 DrissionPage 的核心独特能力，可精确捕获 XHR/Fetch/图片等任意请求。

        \b
        示例:
          dpx listen --filter "api/login"
          dpx listen --method POST
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
    @click.option('--output', default=None,
                  help='把数据包结构化 JSON 写入文件（默认内联到 data.packets）')
    @trim_options
    def listen_stop(session, count, timeout, output, fields, head):
        """停止监听并获取捕获的网络请求数据（含请求头/postData/响应体）。"""
        page = _get_page(session)
        if not getattr(page.listen, 'listening', False):
            error('当前进程无活动监听——listen 是进程内监听器，跨进程不持久。'
                  '请在同一会话的常驻进程内配对使用：'
                  'dpx serve / dpx sh / daemon(DPX_DAEMON=1) / dpx mcp 内 '
                  'listen→触发→listen-stop；'
                  '或改用被动缓冲 `dpx requests`（serve/daemon 托管）',
                  code='LISTEN_FAILED')
            return
        try:
            # finally 中 stop：wait 超时/出错也必须停止监听，否则监听器残留
            try:
                packets = page.listen.wait(count=count, timeout=timeout, fit_count=False)
            finally:
                page.listen.stop()
            results = []
            if packets:
                pkts = packets if isinstance(packets, list) else [packets]
                for pkt in pkts:
                    try:
                        req = getattr(pkt, 'request', None)
                        item = {
                            'url': pkt.url,
                            'method': pkt.method,
                            'status': pkt.response.status if pkt.response else None,
                            'type': pkt.resourceType,
                        }
                        # 逆向取证关键字段：请求头与 postData（签名入参分析）
                        try:
                            if req is not None:
                                item['request_headers'] = dict(
                                    getattr(req, 'headers', None) or {})
                                pd = getattr(req, 'postData', None)
                                if pd is not None:
                                    item['postData'] = pd
                        except Exception:
                            pass
                        try:
                            if pkt.response:
                                item['response_headers'] = dict(
                                    getattr(pkt.response, 'headers', None) or {})
                        except Exception:
                            pass
                        try:
                            item['response_body'] = pkt.response.body
                        except Exception as e:
                            debug(f'读取响应体失败 url={item["url"]}', e)
                        results.append(item)
                    except Exception:
                        continue
            results, total = shape_records(results, fields, head,
                                           _PACKET_FIELDS)
            data = {'packets': results, 'count': len(results)}
            if head is not None:
                data['total'] = total
            if output:
                Path(output).write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding='utf-8')
                ok({'saved_to': output, 'count': len(results)})
            else:
                ok(data)
        except Exception as e:
            error(f'获取监听数据失败', code='LISTEN_FAILED', detail=str(e))

    @cli.command('har-dump')
    @session_option
    @click.option('--seconds', type=float, default=5, show_default=True,
                  help='收集窗口秒数（期间触发的请求被聚合为 HAR）')
    @click.option('--filter', 'url_filter', default=None,
                  help='URL 关键字过滤，如 "api/"')
    @click.option('--output', default=None,
                  help='HAR 写入文件路径（默认内联到 JSON data.har）')
    @click.option('--body', 'with_body', is_flag=True,
                  help='抓取响应 body（逐条 Network.getResponseBody，较慢）')
    def har_dump(session, seconds, url_filter, output, with_body):
        """录制网络活动并导出 HAR 1.2（entries/timings/可选响应体）。

        开启 Network domain 事件收集，等待 --seconds 秒后聚合输出；
        期间可通过其它进程/手动操作触发页面请求。

        \b
        示例:
          dpx har-dump --seconds 10 --output trace.har
          dpx har-dump --seconds 8 --filter "api/" --body
        """
        page = _get_page(session)
        entries = {}

        def _on_request(**kw):
            rid = kw.get('requestId')
            req = kw.get('request') or {}
            url = req.get('url', '')
            if url_filter and url_filter not in url:
                return
            e = {
                'startedDateTime': _iso_ts(kw.get('wallTime')),
                '_resp_ts': None, '_end_ts': None,
                'request': {
                    'method': req.get('method', 'GET'),
                    'url': url,
                    'httpVersion': 'HTTP/1.1',
                    'headers': _hdr_list(req.get('headers')),
                    'queryString': [],
                    'headersSize': -1,
                    'bodySize': len(req.get('postData') or ''),
                },
                'response': {
                    'status': 0, 'statusText': '', 'httpVersion': '',
                    'headers': [], 'content': {}, 'redirectURL': '',
                    'headersSize': -1, 'bodySize': -1,
                },
                'cache': {},
                'timings': {'send': 0, 'wait': 0, 'receive': 0},
                'time': 0,
            }
            if req.get('postData') is not None:
                e['request']['postData'] = {
                    'mimeType': (req.get('headers') or {}).get(
                        'Content-Type', 'application/octet-stream'),
                    'text': req['postData'],
                }
            entries[rid] = e

        def _on_response(**kw):
            e = entries.get(kw.get('requestId'))
            if not e:
                return
            resp = kw.get('response') or {}
            resp_headers = resp.get('headers') or {}
            e['response'] = {
                'status': resp.get('status', 0),
                'statusText': resp.get('statusText', ''),
                'httpVersion': resp.get('protocol', 'http/1.1'),
                'headers': _hdr_list(resp_headers),
                'content': {'size': -1,
                            'mimeType': resp.get('mimeType', '')},
                'redirectURL': str(resp_headers.get('location')
                                   or resp_headers.get('Location') or ''),
                'headersSize': -1, 'bodySize': -1,
            }
            e['_timing'] = resp.get('timing') or {}
            e['_resp_ts'] = kw.get('timestamp')

        def _on_finished(**kw):
            e = entries.get(kw.get('requestId'))
            if not e:
                return
            size = kw.get('encodedDataLength', -1)
            e['response']['bodySize'] = size
            e['response']['content']['size'] = size
            e['_end_ts'] = kw.get('timestamp')
            e['_done'] = True

        def _on_failed(**kw):
            e = entries.get(kw.get('requestId'))
            if not e:
                return
            e['_end_ts'] = kw.get('timestamp')
            e['_done'] = True
            e['response']['statusText'] = kw.get('errorText', 'failed')
            e['_failed'] = True

        try:
            page.run_cdp('Network.enable')
            driver = page.driver
            driver.set_callback('Network.requestWillBeSent', _on_request)
            driver.set_callback('Network.responseReceived', _on_response)
            driver.set_callback('Network.loadingFinished', _on_finished)
            driver.set_callback('Network.loadingFailed', _on_failed)
            try:
                end = perf_counter() + seconds
                while perf_counter() < end:
                    _sleep(0.1)
            finally:
                for ev in ('Network.requestWillBeSent',
                           'Network.responseReceived',
                           'Network.loadingFinished',
                           'Network.loadingFailed'):
                    driver.set_callback(ev, None)

            # 可选：逐条补抓响应体（仅对已完成的请求）
            if with_body:
                for rid, e in entries.items():
                    if not e.get('_done') or e.get('_failed'):
                        continue
                    try:
                        b = page.run_cdp('Network.getResponseBody',
                                         requestId=rid)
                        e['response']['content']['text'] = b.get('body', '')
                        if b.get('base64Encoded'):
                            e['response']['content']['encoding'] = 'base64'
                    except Exception as ex:
                        debug(f'getResponseBody 失败 url={e["request"]["url"]}',
                              ex)

            har = _build_har(entries, getattr(page, 'url', '') or '')
            if output:
                Path(output).write_text(
                    json.dumps(har, ensure_ascii=False, indent=2),
                    encoding='utf-8')
                ok({'saved_to': output,
                    'entries': len(har['log']['entries'])})
            else:
                ok({'har': har, 'entries': len(har['log']['entries'])})
        except Exception as e:
            error('HAR 收集失败', code='LISTEN_FAILED', detail=str(e))

    @cli.command('http-get')
    @click.argument('url')
    @click.option('--headers', default=None, help='JSON 格式的 Headers')
    @click.option('--proxy', default=None, help='代理地址')
    @click.option('--timeout', default=30, help='超时秒数', show_default=True)
    @click.option('--output', default=None, help='响应体保存路径')
    @click.option('--impersonate', default=None,
                  help='模拟浏览器 TLS 指纹（需 curl_cffi，如 chrome131 / '
                       'safari18_0；chrome=最新版）。缺包时降级 requests 并告警')
    def http_get(url, headers, proxy, timeout, output, impersonate):
        """发送 HTTP GET 请求（不启动浏览器，高效爬虫模式）。

        \b
        示例:
          dpx http-get https://api.example.com/users
          dpx http-get https://example.com --output page.html
          dpx http-get https://api.example.com --headers '{"Authorization":"Bearer xxx"}'
          dpx http-get https://example.com --impersonate chrome131
        """
        try:
            hdrs = json.loads(headers) if headers else None
            final_url, resp, warnings = _http_send(
                url, 'GET', hdrs, proxy, timeout, impersonate)
            result = _emit_response(final_url, resp, output)
            if warnings:
                result['warnings'] = warnings
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
    @click.option('--output', default=None, help='响应体保存路径')
    @click.option('--impersonate', default=None,
                  help='模拟浏览器 TLS 指纹（需 curl_cffi，如 chrome131）。'
                       '缺包时降级 requests 并告警')
    def http_post(url, data, form, headers, proxy, timeout, output, impersonate):
        """发送 HTTP POST 请求（不启动浏览器）。

        \b
        示例:
          dpx http-post https://api.example.com/login --data '{"user":"admin","pass":"123"}'
          dpx http-post https://example.com/form --form '{"field":"value"}'
          dpx http-post https://api.example.com --impersonate chrome131 --data '{}'
        """
        try:
            hdrs = json.loads(headers) if headers else None
            final_url, resp, warnings = _http_send(
                url, 'POST', hdrs, proxy, timeout, impersonate,
                data=json.loads(data) if data else None,
                form=json.loads(form) if form else None)
            result = _emit_response(final_url, resp, output)
            if warnings:
                result['warnings'] = warnings
            ok(result)
        except Exception as e:
            error(f'HTTP POST 失败', code='HTTP_FAILED', detail=str(e))
