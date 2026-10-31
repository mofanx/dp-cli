# -*- coding:utf-8 -*-
"""网络命令: listen / listen-stop / requests / request / api-list / api /
api-record / http-get / http-post / har-dump"""
import json
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter, sleep as _sleep
from urllib.parse import urlsplit, parse_qsl, quote

import click

from dp_cli.output import ok, error, debug
from dp_cli.commands._utils import (session_option, _get_page,
                                    trim_options, shape_records)

_PACKET_FIELDS = {'url', 'method', 'status', 'type', 'request_headers',
                  'postData', 'response_headers', 'response_body'}

# api-list 输出字段白名单（--fields 校验用）
_API_FIELDS = {'idx', 'id', 'method', 'url', 'url_template', 'params_keys',
               'content_type', 'status', 'failed'}
_API_TYPES = {'xhr', 'fetch'}   # CDP ResourceType 小写比较
# curl 导出时剔除的头：HTTP/2 伪头（:authority 等）curl 不认；
# content-length 由 curl 按 --data 自算，硬编码会与 postData 长度漂移
_SKIP_CURL_HEADERS = {'content-length'}
# dpx api 内联 body 截断上限（防大响应炸 stdout；--raw/--output 绕行）
_API_BODY_LIMIT = 64 * 1024


def _parse_api_headers(header_list) -> dict:
    """-H 'Name: value' 列表 → headers dict；格式非法经 error() 退出。"""
    headers = {}
    for h in header_list or ():
        if ':' not in h:
            error(f'-H 格式错误（需 "Name: value"）: {h!r}',
                  code='INVALID_ARGS')
        k, v = h.split(':', 1)
        k = k.strip()
        if not k:
            error(f'-H header 名为空: {h!r}', code='INVALID_ARGS')
        headers[k] = v.strip()
    return headers


def _build_api_fetch_js(url, method, headers, body) -> str:
    """构造页内 fetch 调用脚本（run_js function 包裹 + awaitPromise 解析）。

    返回 promise → DP 的 Runtime.callFunctionOn awaitPromise=True 会把
    resolve 值回填为返回值；fetch reject（网络错误/CORS/ abort）→
    exceptionDetails → JavaScriptError，由调用方映射 API_FETCH_FAILED。
    """
    opts = {'method': method}
    if headers:
        opts['headers'] = headers
    if body is not None:
        opts['body'] = body
    return (
        f'return fetch({json.dumps(url, ensure_ascii=False)},'
        f'{json.dumps(opts, ensure_ascii=False)})'
        '.then(function(r){return r.text().then(function(t){'
        'return{status:r.status,ok:r.ok,url:r.url,body:t}})})'
    )


def _requests_no_buffer(session):
    error('无被动网络日志（需要常驻进程托管事件缓冲；'
          '或使用 dpx listen 主动监听）', code='NO_NETLOG',
          detail=f'dpx serve --session {session} 或 '
                 f'dpx daemon-start -S {session} + DPX_DAEMON=1')


def _netlog_rows(entries):
    """netlog 条目按 requestId 聚合为行（时间序，dict 保序）。

    请求/响应头分开存 request_headers/response_headers——若共用 'headers'
    键，后到站的 resp 会覆盖 req 头，导出 curl 会错拿响应头当请求头。
    """
    reqs = {}
    for e in entries:
        rid = e.get('id')
        if rid is None:
            continue
        slot = reqs.setdefault(rid, {'id': rid})
        kind = e.get('kind')
        if kind == 'req':
            for k in ('url', 'method', 'type', 'postData', 'ts', 'wts'):
                if e.get(k) is not None:
                    slot[k] = e[k]
            if e.get('headers') is not None:
                slot['request_headers'] = e['headers']
        elif kind == 'resp':
            if e.get('status') is not None:
                slot['status'] = e['status']
            if e.get('statusText') is not None:
                slot['statusText'] = e['statusText']
            if e.get('mimeType') is not None:
                slot['mimeType'] = e['mimeType']
            if e.get('headers') is not None:
                slot['response_headers'] = e['headers']
            if slot.get('url') is None and e.get('url') is not None:
                slot['url'] = e['url']
        elif kind == 'fail':
            slot['failed'] = e.get('errorText') or 'failed'
    return list(reqs.values())


def _row_match(r, furl, fmethod, ftype, ffailed, fstatus) -> bool:
    """requests/api-list 共用行级过滤谓词
    （URL 子串/method/type/status/failed，全 None 恒真）。"""
    if furl and furl not in (r.get('url') or ''):
        return False
    if fmethod and (r.get('method') or '').upper() != fmethod.upper():
        return False
    if ftype and (r.get('type') or '').lower() != ftype.lower():
        return False
    if fstatus is not None and r.get('status') != fstatus:
        return False
    if ffailed and not r.get('failed'):
        return False
    return True


def _export_curl(row) -> str:
    """单行聚合记录 → curl 命令文本。"""
    method = (row.get('method') or 'GET').upper()
    parts = ['curl', '-X', method, shlex.quote(row.get('url') or '')]
    for k, v in (row.get('request_headers') or {}).items():
        kl = str(k).lower()
        if kl.startswith(':') or kl in _SKIP_CURL_HEADERS:
            continue
        parts += ['-H', shlex.quote(f'{k}: {v}')]
    post = row.get('postData')
    if post is not None:
        parts += ['--data-raw', shlex.quote(str(post))]
    return ' '.join(parts)


def _export_python(row) -> str:
    """单行聚合记录 → python requests 片段。"""
    method = (row.get('method') or 'GET').upper()
    headers = {k: v for k, v in (row.get('request_headers') or {}).items()
               if not str(k).startswith(':')}
    lines = ['import requests', '',
             f'resp = requests.request({method!r}, {row.get("url") or ""!r},']
    if headers:
        lines.append(f'    headers={headers!r},')
    if row.get('postData') is not None:
        lines.append(f'    data={row["postData"]!r},')
    lines += [')', 'print(resp.status_code, resp.text[:500])']
    return '\n'.join(lines)


def _header_get(headers, name: str):
    """headers dict 大小写不敏感取值。"""
    if not isinstance(headers, dict):
        return None
    for k, v in headers.items():
        if str(k).lower() == name:
            return v
    return None


def _api_endpoint(idx: int, row: dict) -> dict:
    """聚合行 → api-list 端点条目；query 参数值抽成 {key} 模板位。"""
    url = row.get('url') or ''
    keys = []
    template = url
    try:
        sp = urlsplit(url)
        pairs = parse_qsl(sp.query, keep_blank_values=True)
        keys = [k for k, _ in pairs]
        base = (f'{sp.scheme}://{sp.netloc}{sp.path}'
                if sp.scheme else sp.path)
        if pairs:
            q = '&'.join(f'{k}={{{k}}}' for k, _ in pairs)
            template = f'{base}?{q}'
        else:
            template = base or url
    except Exception:
        pass
    item = {'idx': idx, 'id': row.get('id'),
            'method': (row.get('method') or 'GET').upper(),
            'url': url, 'url_template': template,
            'params_keys': keys,
            'content_type': _header_get(row.get('response_headers'),
                                        'content-type')
                            or row.get('mimeType'),
            'status': row.get('status')}
    if row.get('failed'):
        item['failed'] = row['failed']
    return item


# ── 录制式 adapter 层（api-record 固化 / dpx api <name> 重放）─────────
# adapter 注册表目录——惰性求值（HOME 隔离 seam，同 netlog._path 教训）
_ADAPTER_NAME_RE = re.compile(r'^[A-Za-z0-9][\w-]{0,63}$')
_TEMPLATE_SLOT_RE = re.compile(r'\{([^{}]+)\}')
_UI_FB_MAX_STEPS = 8        # UI 回退链 decide→执行 步数上限（防死循环）


def _adapters_dir() -> Path:
    return Path.home() / '.dp_cli' / 'adapters'


def _adapter_path(name: str):
    """合法 adapter 名 → 注册表路径；非法名（含路径分隔符等）None——
    name/URL 判定共用：URL 形态（含 / :）天然不匹配正则走 URL 分支。"""
    if not name or not _ADAPTER_NAME_RE.match(name):
        return None
    return _adapters_dir() / f'{name}.json'


def _load_adapter(name: str):
    """命中注册表返回 adapter dict，否则 None（未命中即按 URL 处理）。"""
    p = _adapter_path(name)
    if p is None or not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def _list_adapters() -> list:
    """api-list --adapters / api-record --list 共用注册表快照。"""
    d = _adapters_dir()
    out = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob('*.json')):
        try:
            a = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            a = {}
        ep = a.get('endpoint') or {}
        fb = a.get('ui_fallback') or {}
        out.append({'name': a.get('name') or p.stem,
                    'recorded_at': a.get('recorded_at'),
                    'method': (ep.get('method') or 'GET').upper(),
                    'url_template': ep.get('url_template'),
                    'params_keys': ep.get('params_keys') or [],
                    'ui_fallback': fb or None})
    return out


def _build_url_template(url: str, param_map: dict) -> tuple:
    """录制端：query 参数值 → {模板变量} 占位。

    :param param_map: {原始 query 键: 模板变量名}（--param kw=keyword）
    :return: (url_template, params_keys)；映射键不在 query 中抛 ValueError
    """
    sp = urlsplit(url)
    pairs = parse_qsl(sp.query, keep_blank_values=True)
    have = {k for k, _ in pairs}
    bad = [k for k in param_map if k not in have]
    if bad:
        raise ValueError(
            f'--param 映射的 query 键不存在于录制 URL: {", ".join(bad)}'
            f'（实际: {", ".join(sorted(have)) or "无"}）')
    keys = []
    base = (f'{sp.scheme}://{sp.netloc}{sp.path}'
            if sp.scheme else sp.path)
    if not pairs:
        return base or url, keys
    q = []
    for k, v in pairs:
        if k in param_map:
            var = param_map[k]
            q.append(f'{k}={{{var}}}')
            keys.append(var)
        else:
            q.append(f'{k}={v}')
    return f'{base}?{"&".join(q)}', keys


def _render_template(template: str, params: dict) -> tuple:
    """重放端：{name} 位插值（值 URL 编码）→ (url, missing_keys)。"""
    missing = []

    def _rep(m):
        k = m.group(1)
        if k in params:
            return quote(str(params[k]), safe='')
        missing.append(k)
        return m.group(0)

    return _TEMPLATE_SLOT_RE.sub(_rep, template or ''), missing


def _parse_kv_args(args) -> dict:
    """dpx api <name> kw=v 位置参数 → dict；非 k=v 形经 error() 退出。"""
    out = {}
    for a in args or ():
        if '=' not in a:
            error(f'adapter 参数须为 k=v 形: {a!r}', code='INVALID_ARGS')
        k, v = a.split('=', 1)
        if not k:
            error(f'adapter 参数键为空: {a!r}', code='INVALID_ARGS')
        out[k] = v
    return out


def _parse_param_map(items) -> dict:
    """api-record --param kw=keyword → {query键: 模板变量}；非法经 error()。"""
    out = {}
    for it in items or ():
        if '=' not in it:
            error(f'--param 须为 query键=模板名 形（如 kw=keyword）: {it!r}',
                  code='INVALID_ARGS')
        k, v = it.split('=', 1)
        k, v = k.strip(), v.strip()
        if not k or not v:
            error(f'--param 键/变量名不能为空: {it!r}', code='INVALID_ARGS')
        if not re.fullmatch(r'[A-Za-z_]\w*', v):
            error(f'--param 模板变量名非法（须标识符）: {v!r}',
                  code='INVALID_ARGS')
        out[k] = v
    return out


def _decide_action_argv(data: dict, session: str, value: str):
    """decide 返回 {action,ref} → 可执行 argv；done/未知动作返回 None。

    动作映射（与 decide _ACTIONS 对齐）：
      click→click ref:N  type/fill→fill ref:N <kv 值>
      select→select      scroll_*→scroll  back→go-back  wait→sleep
    """
    act = (data.get('action') or '').lower()
    ref = data.get('ref')
    if act in ('click', 'tap', 'press_enter'):
        return (['click', f'ref:{ref}', '-S', session]
                if ref is not None else None)
    if act in ('type', 'type_text', 'fill', 'input'):
        return (['fill', f'ref:{ref}', value or '', '-S', session]
                if ref is not None else None)
    if act == 'select':
        return (['select', f'ref:{ref}', value or '', '-S', session]
                if ref is not None else None)
    if act == 'scroll_up':
        return ['scroll', '--y', '-600', '-S', session]
    if act == 'scroll_down':
        return ['scroll', '--y', '600', '-S', session]
    if act == 'back':
        return ['go-back', '-S', session]
    if act == 'wait':
        return ['sleep', '1']
    return None


def _run_ui_fallback(fb: dict, session: str, kv: dict) -> tuple:
    """UI 回退链：open nav_url → decide goal → 动作执行循环（fail-closed）。

    全程 in-process _invoke（serve/daemon/CLI 三渠道语义一致）；
    decide done→(True,info)，链内任一步失败/步数耗尽→(False,info)。
    """
    from dp_cli.commands.batch import _invoke
    sess = fb.get('session') or session
    nav = fb.get('nav_url')
    goal = fb.get('goal')
    steps = []
    if nav:
        code, res = _invoke(['open', nav, '-S', sess])
        steps.append({'cmd': f'open {nav}', 'exit': code})
        if code != 0:
            return False, {'steps': steps,
                           'error': 'nav_url 打开失败', 'result': res}
    if not goal:
        # 仅导航形态——到页即止（人工/上层续走），如实标 partial
        return True, {'steps': steps, 'partial': True}
    # type/fill/select 动作的填充值：adapter 参数值优先，goal 兜底
    value = ' '.join(str(v) for v in kv.values()) or (goal or '')
    for _ in range(_UI_FB_MAX_STEPS):
        code, res = _invoke(['decide', goal, '-S', sess])
        if code != 0:
            steps.append({'cmd': f'decide {goal}', 'exit': code,
                          'result': res})
            return False, {'steps': steps, 'error': 'decide 失败'}
        data = (res or {}).get('data') or {}
        act = (data.get('action') or '').lower()
        steps.append({'cmd': f'decide {goal}', 'exit': code,
                      'action': act, 'ref': data.get('ref')})
        if data.get('done') or act == 'done':
            return True, {'steps': steps}
        if act == 'blocked' or data.get('stuck'):
            return False, {'steps': steps,
                           'error': f'回退链中止: {act or "stuck"}'}
        argv = _decide_action_argv(data, sess, value)
        if argv is None:
            return False, {'steps': steps,
                           'error': f'不可执行的 decide 动作: {act}'}
        code, res = _invoke(argv)
        steps.append({'cmd': ' '.join(argv), 'exit': code})
        if code != 0:
            return False, {'steps': steps,
                           'error': '回退动作执行失败', 'result': res}
    return False, {'steps': steps,
                   'error': f'步数上限 {_UI_FB_MAX_STEPS} 未收敛'}


def _register_requests(cli):
    @cli.command('requests')
    @session_option
    @click.option('--filter', 'furl', default=None,
                  help='URL 子串过滤（如 "api/"）')
    @click.option('--method', 'fmethod', default=None,
                  help='HTTP 方法过滤（GET/POST/...）')
    @click.option('--type', 'ftype', default=None,
                  help='资源类型过滤（XHR/Fetch/Document/...）')
    @click.option('--status', 'fstatus', type=int, default=None,
                  help='HTTP 状态码过滤（如 200/404）')
    @click.option('--failed', 'ffailed', is_flag=True,
                  help='只看失败请求')
    @click.option('--head', type=int, default=None,
                  help='只输出前 N 条（按时间序）')
    @click.option('--index', 'findex', type=int, default=None,
                  help='按完整日志序号选一条（0 起，同 api-list 的 idx；'
                       '指定后其余过滤条件不生效）')
    @click.option('--export', 'export_fmt',
                  type=click.Choice(['curl', 'python']), default=None,
                  help='导出为重放代码：curl 命令行 / python requests 片段')
    @click.option('--all', 'fall', is_flag=True,
                  help='导出全部过滤结果（--export 的默认行为，显式声明用；'
                       '与 --index 互斥）')
    def cmd_requests(session, furl, fmethod, ftype, ffailed, fstatus, head,
                     findex, export_fmt, fall):
        """列出被动网络日志（serve 进程托管的 ~500 条 ring buffer）。

        需先跑 `dpx serve --session <name>`——常驻进程替你开着
        Network.enable 监听滚动写盘；无缓冲时提示改用 dpx listen。

        \b
        示例:
          dpx requests                       # 全部（时间序）
          dpx requests --filter api/         # URL 过滤
          dpx requests --method POST --failed
          dpx requests --export curl               # 全部导出 curl
          dpx requests --export curl --index 3     # 导出第 3 条
          dpx requests --export python --filter api/
        """
        from dp_cli.netlog import read_entries
        entries = read_entries(session)
        if entries is None:
            _requests_no_buffer(session)
            return
        if findex is not None and fall:
            error('--index 与 --all 互斥', code='CONFLICTING_OPTIONS')
            return
        # 按 requestId 聚合 req+resp+fail 成行（时间序）
        all_rows = _netlog_rows(entries)
        if findex is not None:
            # --index 走完整日志序号（与 api-list idx 同序），短路过滤链
            if not 0 <= findex < len(all_rows):
                error(f'--index 越界: {findex}（共 {len(all_rows)} 条）',
                      code='REQUEST_NOT_FOUND')
                return
            targets = [(findex, all_rows[findex])]
        else:
            # idx = 完整日志序号（与 api-list 的 idx 同序，可互查重放）
            targets = [(i, r) for i, r in enumerate(all_rows)
                       if _row_match(r, furl, fmethod, ftype,
                                     ffailed, fstatus)]
        if export_fmt:
            # 无 URL 的条目（fail-only 等）不可重放——剔除
            targets = [(i, r) for i, r in targets if r.get('url')]
            if not targets:
                error('无匹配请求可导出（过滤后为空或条目无 URL）',
                      code='EMPTY_LIST')
                return
            fn = _export_curl if export_fmt == 'curl' else _export_python
            exports = [{'idx': i, 'id': r.get('id'),
                        'method': (r.get('method') or 'GET').upper(),
                        'url': r.get('url'),
                        'text': fn(r)} for i, r in targets]
            ok({'format': export_fmt, 'count': len(exports),
                'exports': exports})
            return
        rows = [r for _, r in targets]
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

    @cli.command('api-list')
    @session_option
    @click.option('--filter', 'furl', default=None,
                  help='URL 子串过滤（如 "api/"）')
    @click.option('--method', 'fmethod', default=None,
                  help='HTTP 方法过滤（GET/POST/...）')
    @click.option('--status', 'fstatus', type=int, default=None,
                  help='HTTP 状态码过滤（如 200/404）')
    @click.option('--adapters', 'list_adapters', is_flag=True,
                  help='改列 adapter 注册表（api-record 固化的端点模板）')
    @trim_options
    def cmd_api_list(session, furl, fmethod, fstatus, list_adapters,
                     head, fields):
        """列出 netlog 中可重放的 API 端点（仅 XHR/Fetch，排除 Document
        及静态资源）。

        输出 data.endpoints[]: {idx, id, method, url, url_template,
        params_keys, content_type, status}——query 参数值抽成 {key}
        模板位；idx 与 `dpx requests --index` 同序，可直接
        `dpx requests --export curl --index <idx>` 导出重放命令。

        \b
        示例:
          dpx api-list                     # 全部 XHR/Fetch 端点
          dpx api-list --filter api/       # URL 过滤
          dpx api-list --method POST --status 200
          dpx api-list --adapters          # adapter 注册表
        """
        if list_adapters:
            ads = _list_adapters()
            ok({'count': len(ads), 'adapters': ads})
            return
        from dp_cli.netlog import read_entries
        entries = read_entries(session)
        if entries is None:
            _requests_no_buffer(session)
            return
        rows = _netlog_rows(entries)
        eps = [_api_endpoint(i, r) for i, r in enumerate(rows)
               if (r.get('type') or '').lower() in _API_TYPES
               and _row_match(r, furl, fmethod, None, False, fstatus)]
        if not eps:
            error('netlog 中无 XHR/Fetch 端点（仅有 Document/静态资源'
                  '或尚未产生请求）', code='EMPTY_LIST')
            return
        eps, total = shape_records(eps, fields, head, _API_FIELDS)
        data = {'count': len(eps), 'endpoints': eps}
        if head is not None:
            data['total'] = total
        ok(data)

    @cli.command('api-record')
    @click.argument('name', required=False)
    @session_option
    @click.option('--index', 'findex', type=int, default=None,
                  help='按完整日志序号选 netlog 行（0 起，同 requests '
                       '--index / api-list idx；默认最后一条 XHR/Fetch）')
    @click.option('--param', 'param_map', multiple=True,
                  help='可变参数位映射 query键=模板名（如 kw=keyword），'
                       '可重复——映射的 query 值固化为 {模板名} 占位')
    @click.option('--goal', default=None,
                  help='UI 回退链 goal（decide 目标，可选；'
                       'api 失败时自动降级走 UI 路径）')
    @click.option('--nav-url', 'nav_url', default=None,
                  help='UI 回退目标页 URL（回退链第一步 open 目标）')
    @click.option('--list', 'do_list', is_flag=True,
                  help='列出 adapter 注册表（同 api-list --adapters）')
    @click.option('--delete', 'do_delete', is_flag=True,
                  help='删除指定 adapter：api-record --delete <name>')
    def cmd_api_record(name, session, findex, param_map, goal, nav_url,
                       do_list, do_delete):
        """录制式 adapter：netlog 挑一条 XHR/Fetch 固化为端点模板。

        产物 ~/.dp_cli/adapters/<name>.json——录制一次、重放多次：
        `dpx api <name> kw=v` 按 url_template 插值后走页内 fetch；
        配了 --nav-url/--goal 时 fetch 失败（reject/HTTP≥400）fail-closed
        自动降级走 UI 链（open nav_url→decide goal→执行），返回
        data.via='ui_fallback' 如实标路径。

        \b
        示例:
          dpx api-record bili-search --param kw=keyword \\
              --nav-url https://b.com/search --goal '搜索'
          dpx api-record last-xhr               # 默认最后一条 XHR
          dpx api-record myapi --index 3        # 指定日志行
          dpx api-record --list                 # 注册表
          dpx api-record --delete myapi         # 删除
        """
        if do_list:
            ads = _list_adapters()
            ok({'count': len(ads), 'adapters': ads})
            return
        if do_delete:
            if not name:
                error('--delete 需要 adapter 名: api-record --delete <name>',
                      code='INVALID_ARGS')
                return
            p = _adapter_path(name)
            if p is None:
                error(f'adapter 名非法: {name!r}', code='INVALID_ARGS')
                return
            if not p.exists():
                error(f'adapter 不存在: {name}', code='FILE_NOT_FOUND')
                return
            p.unlink()
            ok({'deleted': name})
            return
        if not name:
            error('缺 adapter 名: api-record <name>', code='INVALID_ARGS')
            return
        if _adapter_path(name) is None:
            error(f'adapter 名非法（须 [A-Za-z0-9][\\w-]{{0,63}}）: {name!r}',
                  code='INVALID_ARGS')
            return
        pmap = _parse_param_map(param_map)
        from dp_cli.netlog import read_entries
        entries = read_entries(session)
        if entries is None:
            _requests_no_buffer(session)
            return
        rows = _netlog_rows(entries)
        if findex is not None:
            if not 0 <= findex < len(rows):
                error(f'--index 越界: {findex}（共 {len(rows)} 条）',
                      code='REQUEST_NOT_FOUND')
                return
            row = rows[findex]
            if (row.get('type') or '').lower() not in _API_TYPES:
                error(f'--index {findex} 不是 XHR/Fetch'
                      f'（type={row.get("type")}），仅支持录制 API 端点',
                      code='INVALID_ARGS')
                return
        else:
            row = next((r for r in reversed(rows)
                        if (r.get('type') or '').lower() in _API_TYPES
                        and r.get('url')), None)
            if row is None:
                error('netlog 中无 XHR/Fetch 端点可录制（先 serve 托管 '
                      '产生请求，或 --index 指定）', code='EMPTY_LIST')
                return
        url = row.get('url') or ''
        if not url:
            error('所选请求无 URL，无法固化', code='INVALID_ARGS')
            return
        try:
            template, keys = _build_url_template(url, pmap)
        except ValueError as e:
            error(str(e), code='INVALID_ARGS')
            return
        adapter = {
            'name': name,
            'recorded_at': datetime.now(timezone.utc).isoformat()
                          .replace('+00:00', 'Z'),
            'endpoint': {
                'method': (row.get('method') or 'GET').upper(),
                'url_template': template,
                'params_keys': keys,
            },
        }
        if row.get('postData') is not None:
            adapter['endpoint']['body'] = row['postData']
        if goal or nav_url:
            adapter['ui_fallback'] = {
                'session': session,
                **({'nav_url': nav_url} if nav_url else {}),
                **({'goal': goal} if goal else {}),
            }
        d = _adapters_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
            # tmp+rename 原子写——读端（api <name>）不会见到半截文件
            tmp = d / f'{name}.json.tmp'
            tmp.write_text(json.dumps(adapter, ensure_ascii=False,
                                      indent=2), encoding='utf-8')
            tmp.replace(d / f'{name}.json')
        except OSError as e:
            error(f'adapter 写盘失败: {e}', code='HTTP_FAILED')
            return
        ok({'saved_to': str(d / f'{name}.json'), 'adapter': adapter},
           msg=f'adapter [{name}] 已固化')


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
            _evs = ('Network.requestWillBeSent',
                    'Network.responseReceived',
                    'Network.loadingFinished',
                    'Network.loadingFailed')
            # set_callback 是单槽覆盖——serve/daemon 托管时 netlog 已
            # 把回调挂在这 3 个事件上。先快照旧回调（DrissionPage
            # driver.event_handlers 是唯一取值口），finally 逐事件
            # 恢复而非置 None，否则 netlog 被动日志永久停写。
            _handlers = getattr(driver, 'event_handlers', None)
            _saved = ({ev: _handlers.get(ev) for ev in _evs}
                      if isinstance(_handlers, dict) else None)
            driver.set_callback('Network.requestWillBeSent', _on_request)
            driver.set_callback('Network.responseReceived', _on_response)
            driver.set_callback('Network.loadingFinished', _on_finished)
            driver.set_callback('Network.loadingFailed', _on_failed)
            try:
                end = perf_counter() + seconds
                while perf_counter() < end:
                    _sleep(0.1)
            finally:
                if _saved is not None:
                    for ev, cb in _saved.items():
                        driver.set_callback(ev, cb)
                else:
                    for ev in _evs:
                        driver.set_callback(ev, None)
                    # 无 event_handlers 快照口（非常规 driver）——
                    # netlog 回调已被清掉，强制重挂恢复被动日志
                    try:
                        from dp_cli.netlog import arm_netlog
                        arm_netlog(page, session, force=True)
                    except Exception:
                        pass

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
                # tmp+os.replace 原子产出——写一半中断不留半成品 HAR
                # （同 cookie-export --output 模式）
                import os as _os
                _p = Path(output)
                _tmp = _p.with_name(_p.name + '.tmp')
                _tmp.write_text(
                    json.dumps(har, ensure_ascii=False, indent=2),
                    encoding='utf-8')
                _os.replace(_tmp, _p)
                ok({'saved_to': output,
                    'entries': len(har['log']['entries'])})
            else:
                ok({'har': har, 'entries': len(har['log']['entries'])})
        except Exception as e:
            error('HAR 收集失败', code='LISTEN_FAILED', detail=str(e))

    @cli.command('api')
    @click.argument('url')
    @click.argument('kv', nargs=-1)
    @session_option
    @click.option('-X', '--request', 'method', default=None,
                  help='HTTP 方法（GET/POST/PUT/...，默认 GET；'
                       'adapter 形态默认取录制方法）')
    @click.option('-H', '--header', 'headers', multiple=True,
                  help="请求头 'Name: value'，可重复")
    @click.option('-d', '--data', 'data', default=None,
                  help='请求体（原文发送；adapter POST 默认取录制 body）')
    @click.option('--json', 'json_body', default=None,
                  help='JSON 请求体（自动序列化+'
                       'Content-Type: application/json）')
    @click.option('--raw', is_flag=True,
                  help=f'body 不截断（默认内联上限 '
                       f'{_API_BODY_LIMIT // 1024}KB）')
    @click.option('--output', default=None,
                  help='响应体写入文件（不内联到 data.body）')
    @click.option('--timeout', default=30,
                  help='fetch 执行超时秒数', show_default=True)
    def api_cmd(url, kv, session, method, headers, data, json_body,
                raw, output, timeout):
        """在当前页面上下文内执行 fetch（页内同源请求）。

        借页面同源上下文：自带 Cookie/UA/Referer/CSRF token，
        可命中需要登录态的接口。跨源受 CORS 约束——fetch reject
        报 API_FETCH_FAILED。

        \b
        adapter 形态：首参命中 ~/.dp_cli/adapters/<name>.json 时按
        adapter 处理——url_template 用 kw=v 位置参数插值重建 URL；
        fetch reject/超时/HTTP≥400 且 adapter 配了 ui_fallback 时
        fail-closed 自动降级走 UI 链（open nav_url→decide goal→执行），
        返回 data.via='ui_fallback'（成功 via='api' 如实标路径）。

        \b
        状态码契约：fetch 成功即 ok——http_status 是数据（404/500
        照常在 data.http_status 返回，ok=False 标记非 2xx）；
        仅 fetch 抛错才 error（adapter+ui_fallback 例外：降级非报错）。

        \b
        示例:
          dpx api /api/user                        # 相对路径=当前页同源
          dpx api https://a.com/login -X POST \\
              -H 'X-CSRF: t' --json '{"u":"a"}'
          dpx api /report.csv --output r.csv       # 大响应写文件
          dpx api /api/big --raw                   # 关闭 64KB 截断
          dpx api bili-search keyword=python       # adapter 重放
        """
        kv_params = _parse_kv_args(kv)
        adapter = _load_adapter(url)
        warnings = []
        if adapter is not None:
            ep = adapter.get('endpoint') or {}
            if not ep.get('url_template'):
                error(f'adapter [{url}] 损坏：缺 endpoint.url_template'
                      '（api-record --delete 后重录）',
                      code='INVALID_ARGS')
                return
            rebuilt, missing = _render_template(
                ep.get('url_template'), kv_params)
            if missing:
                error(f'adapter [{url}] 缺模板参数: {", ".join(missing)}'
                      f'（需要: {", ".join(ep.get("params_keys") or [])}'
                      f'——形如 {url} key=value）',
                      code='INVALID_ARGS')
                return
            extra = [k for k in kv_params
                     if k not in set(_TEMPLATE_SLOT_RE.findall(
                         ep.get('url_template') or ''))]
            if extra:
                warnings.append(
                    f'参数未在 url_template 占位中，已忽略: '
                    f'{", ".join(extra)}')
            url = rebuilt
            if method is None:
                method = ep.get('method')
            if data is None and json_body is None \
                    and ep.get('body') is not None:
                data = ep['body']
        elif kv_params:
            error('URL 形态不接受 kw=v 位置参数（kw=v 仅 adapter '
                  '形态使用；query 请直接写进 URL）', code='INVALID_ARGS')
            return
        if data is not None and json_body is not None:
            error('-d/--data 与 --json 互斥', code='CONFLICTING_OPTIONS')
            return
        method = (method or 'GET').upper()
        body = data
        hdrs = _parse_api_headers(headers)
        if json_body is not None:
            try:
                # 紧凑分隔——与 JSON.stringify 输出逐字节一致
                body = json.dumps(json.loads(json_body),
                                  ensure_ascii=False,
                                  separators=(',', ':'))
            except Exception as e:
                error(f'--json 不是合法 JSON: {e}', code='INVALID_JSON')
                return
            hdrs.setdefault('Content-Type', 'application/json')
        if body is not None and method in ('GET', 'HEAD'):
            error(f'{method} 不能携带请求体', code='INVALID_ARGS')
            return
        fb = (adapter or {}).get('ui_fallback') or {}
        fb_on = adapter is not None and \
            bool(fb.get('nav_url') or fb.get('goal'))

        def _fallback(reason: str):
            """fail-closed：走 UI 回退链——成功 via=ui_fallback 非报错，
            链失败如实报 API_FETCH_FAILED 附链内步迹。"""
            ok_fb, info = _run_ui_fallback(fb, session, kv_params)
            if ok_fb:
                ok({'via': 'ui_fallback', 'ok': True,
                    'adapter': adapter.get('name'),
                    'goal': fb.get('goal'), 'nav_url': fb.get('nav_url'),
                    'fallback_reason': reason,
                    'steps': info.get('steps', []),
                    **({'partial': True} if info.get('partial') else {})})
                return
            error(f'页内 fetch 失败（{reason}）且 UI 回退链未完成',
                  code='API_FETCH_FAILED',
                  detail=json.dumps(info, ensure_ascii=False)[:3000])

        page = _get_page(session)
        js = _build_api_fetch_js(url, method, hdrs, body)
        fetch_failed = None
        try:
            r = page.run_js(js, timeout=timeout)
        except TimeoutError:
            if fb_on:
                _fallback(f'页内 fetch 超时（{timeout}s）')
                return
            error(f'页内 fetch 超时（{timeout}s）', code='WAIT_TIMEOUT',
                  detail=url)
            return
        except Exception as e:
            fetch_failed = str(e)
            r = None
        if fetch_failed is not None:
            if fb_on:
                _fallback(f'页内 fetch 失败: {fetch_failed}')
                return
            error('页内 fetch 失败（网络错误/CORS 拦截/JS 异常）',
                  code='API_FETCH_FAILED', detail=fetch_failed)
            return
        if not isinstance(r, dict) or 'status' not in r:
            if fb_on:
                _fallback(f'页内 fetch 返回异常: {repr(r)[:200]}')
                return
            error('页内 fetch 返回异常', code='API_FETCH_FAILED',
                  detail=repr(r)[:500])
            return
        status = r.get('status')
        if fb_on and isinstance(status, (int, float)) \
                and not isinstance(status, bool) and status >= 400:
            _fallback(f'HTTP {int(status)} ≥400')
            return
        result = {'http_status': r.get('status'),
                  'ok': r.get('ok'), 'url': r.get('url')}
        if adapter is not None:
            result['via'] = 'api'
            result['adapter'] = adapter.get('name')
        if warnings:
            result['warnings'] = warnings
        body_text = r.get('body') or ''
        if output:
            Path(output).write_text(body_text, encoding='utf-8')
            result['saved_to'] = output
            result['body_length'] = len(body_text)
        elif not raw and len(body_text) > _API_BODY_LIMIT:
            result['body'] = body_text[:_API_BODY_LIMIT]
            result['truncated'] = True
            result['body_length'] = len(body_text)
        else:
            result['body'] = body_text
        ok(result)

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
