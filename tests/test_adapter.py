# -*- coding:utf-8 -*-
"""录制式 adapter 层测试：api-record 固化 / dpx api <name> 重放 /
fail-closed ui_fallback 降级链 / serve {"action":"api"} 路由。

全程 mock——adapter json 读写走 isolated_home，页内 fetch 走
FetchPage，UI 回退链断言 _invoke 调用序列，不依赖真浏览器/网络。
"""
import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

import dp_cli.commands.network as net_mod
import dp_cli.commands.serve as serve_mod
import dp_cli.commands.batch as batch_mod


class FetchPage:
    """捕获 run_js 脚本并按预设返回 fetch 结果。"""

    def __init__(self, ret=None, exc=None):
        self.scripts = []
        self._ret = ret if ret is not None else {
            'status': 200, 'ok': True,
            'url': 'https://api.x.com/search?kw=py', 'body': '{"hits":3}'}
        self._exc = exc

    def run_js(self, script, *a, **k):
        self.scripts.append(script)
        if self._exc is not None:
            raise self._exc
        return self._ret


def _fetch_args(script):
    m = re.match(r'return fetch\((\{.*?\}|".*?"),(\{.*\})\)\.then',
                 script)
    assert m, f'脚本形状不符: {script}'
    return json.loads(m.group(1)), json.loads(m.group(2))


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def adir(isolated_home):
    """隔离 HOME 下的 adapter 注册表目录。"""
    d = isolated_home / '.dp_cli' / 'adapters'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_adapter(adir, name='bili-search', **over):
    a = {'name': name, 'recorded_at': '2026-01-01T00:00:00Z',
         'endpoint': {
             'method': 'GET',
             'url_template': 'https://api.x.com/search?kw={keyword}&p=1',
             'params_keys': ['keyword']}}
    a.update(over)
    (adir / f'{name}.json').write_text(
        json.dumps(a, ensure_ascii=False), encoding='utf-8')
    return a


def _invoke(runner, monkeypatch, page, argv):
    monkeypatch.setattr(net_mod, '_get_page', lambda s: page)
    from dp_cli.main import cli
    return runner.invoke(cli, argv)


def _netlog_entries(**kw):
    """构造一条 XHR netlog（req+resp 两行）。"""
    base = [{'kind': 'req', 'id': '1',
             'url': 'https://api.x.com/search?kw=java&p=1',
             'method': 'GET', 'type': 'XHR', 'ts': 1.0},
            {'kind': 'resp', 'id': '1', 'status': 200}]
    base[0].update(kw)
    return base


def _mock_netlog(monkeypatch, entries):
    monkeypatch.setattr('dp_cli.netlog.read_entries',
                        lambda session: entries)


# ── api-record 固化 ─────────────────────────────────────────────

@pytest.mark.unit
def test_api_record_writes_json(runner, adir, monkeypatch):
    """默认取最后一条 XHR；--param 映射固化为 {模板名} 占位。"""
    entries = _netlog_entries() + [
        {'kind': 'req', 'id': '2', 'url': 'https://x.com/doc',
         'method': 'GET', 'type': 'Document', 'ts': 2.0}]
    _mock_netlog(monkeypatch, entries)
    from dp_cli.main import cli
    r = runner.invoke(cli, [
        'api-record', 'bili-search', '--param', 'kw=keyword',
        '--goal', '搜索', '--nav-url', 'https://b.com/s'])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    f = adir / 'bili-search.json'
    assert f.exists()
    d = json.loads(f.read_text(encoding='utf-8'))
    assert d['name'] == 'bili-search'
    assert d['recorded_at']
    ep = d['endpoint']
    assert ep['method'] == 'GET'
    assert ep['url_template'] == \
        'https://api.x.com/search?kw={keyword}&p=1'
    assert ep['params_keys'] == ['keyword']
    assert d['ui_fallback'] == {
        'session': 'default', 'nav_url': 'https://b.com/s',
        'goal': '搜索'}
    # 最后一条是 Document——确认选中的仍是 XHR 行
    assert '{keyword}' in d['endpoint']['url_template']


@pytest.mark.unit
def test_api_record_index_selects_row(runner, adir, monkeypatch):
    """--index 走完整日志序号（同 requests/api-list 序）。"""
    entries = [
        {'kind': 'req', 'id': '1', 'url': 'https://a.com/first',
         'method': 'GET', 'type': 'XHR', 'ts': 1.0},
        {'kind': 'req', 'id': '2', 'url': 'https://a.com/second?x=9',
         'method': 'POST', 'type': 'Fetch', 'ts': 2.0,
         'postData': '{"a":1}'}]
    _mock_netlog(monkeypatch, entries)
    from dp_cli.main import cli
    r = runner.invoke(cli, ['api-record', 'post-api', '--index', '1'])
    assert r.exit_code == 0, r.output
    d = json.loads((adir / 'post-api.json').read_text(encoding='utf-8'))
    assert d['endpoint']['method'] == 'POST'
    assert d['endpoint']['url_template'] == 'https://a.com/second?x=9'
    assert d['endpoint']['params_keys'] == []
    assert d['endpoint']['body'] == '{"a":1}'
    assert 'ui_fallback' not in d


@pytest.mark.unit
def test_api_record_index_non_xhr_rejected(runner, adir, monkeypatch):
    _mock_netlog(monkeypatch, [
        {'kind': 'req', 'id': '1', 'url': 'https://a.com/d',
         'method': 'GET', 'type': 'Document', 'ts': 1.0}])
    from dp_cli.main import cli
    r = runner.invoke(cli, ['api-record', 'x', '--index', '0'])
    assert r.exit_code == 2
    assert json.loads(r.output)['code'] == 'INVALID_ARGS'


@pytest.mark.unit
def test_api_record_index_oob(runner, adir, monkeypatch):
    _mock_netlog(monkeypatch, _netlog_entries())
    from dp_cli.main import cli
    r = runner.invoke(cli, ['api-record', 'x', '--index', '9'])
    assert json.loads(r.output)['code'] == 'REQUEST_NOT_FOUND'


@pytest.mark.unit
def test_api_record_param_key_not_in_query(runner, adir, monkeypatch):
    """--param 映射不存在的 query 键 → INVALID_ARGS。"""
    _mock_netlog(monkeypatch, _netlog_entries())
    from dp_cli.main import cli
    r = runner.invoke(cli, ['api-record', 'x', '--param', 'nope=v'])
    assert r.exit_code == 2
    assert 'nope' in json.loads(r.output)['message']


@pytest.mark.unit
def test_api_record_bad_name_and_no_netlog(runner, adir, monkeypatch):
    from dp_cli.main import cli
    r = runner.invoke(cli, ['api-record', 'a/b'])
    assert json.loads(r.output)['code'] == 'INVALID_ARGS'
    _mock_netlog(monkeypatch, None)          # 无 netlog 缓冲
    r = runner.invoke(cli, ['api-record', 'ok-name'])
    assert json.loads(r.output)['code'] == 'NO_NETLOG'


# ── 注册表管理：--list / --delete / api-list --adapters ─────────

@pytest.mark.unit
def test_api_record_list_and_delete(runner, adir, monkeypatch):
    _write_adapter(adir)
    from dp_cli.main import cli
    r = runner.invoke(cli, ['api-record', '--list'])
    out = json.loads(r.output)
    assert out['data']['count'] == 1
    assert out['data']['adapters'][0]['name'] == 'bili-search'
    assert out['data']['adapters'][0]['params_keys'] == ['keyword']

    r = runner.invoke(cli, ['api-list', '--adapters'])
    out = json.loads(r.output)
    assert out['data']['count'] == 1

    r = runner.invoke(cli, ['api-record', '--delete', 'bili-search'])
    assert json.loads(r.output)['data']['deleted'] == 'bili-search'
    assert not (adir / 'bili-search.json').exists()

    r = runner.invoke(cli, ['api-record', '--delete', 'bili-search'])
    assert json.loads(r.output)['code'] == 'FILE_NOT_FOUND'


# ── dpx api <name> 重放 ─────────────────────────────────────────

@pytest.mark.unit
def test_api_adapter_template_render(runner, adir, monkeypatch):
    """adapter 命中 → url_template 插值重建 → 页内 fetch。"""
    _write_adapter(adir)
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page,
                ['api', 'bili-search', 'keyword=py'])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out['data']['via'] == 'api'
    assert out['data']['adapter'] == 'bili-search'
    url, opts = _fetch_args(page.scripts[0])
    assert url == 'https://api.x.com/search?kw=py&p=1'
    assert opts['method'] == 'GET'


@pytest.mark.unit
def test_api_adapter_param_encoded(runner, adir, monkeypatch):
    """插值经 URL 编码——空格/中文不撕裂 query。"""
    _write_adapter(adir)
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page,
                ['api', 'bili-search', 'keyword=a b'])
    assert r.exit_code == 0, r.output
    url, _ = _fetch_args(page.scripts[0])
    assert 'kw=a%20b' in url


@pytest.mark.unit
def test_api_adapter_missing_param(runner, adir, monkeypatch):
    """模板参数缺失 → INVALID_ARGS 且不发起 fetch。"""
    _write_adapter(adir)
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page, ['api', 'bili-search'])
    assert r.exit_code == 2
    out = json.loads(r.output)
    assert out['code'] == 'INVALID_ARGS'
    assert 'keyword' in out['message']
    assert not page.scripts


@pytest.mark.unit
def test_api_adapter_extra_param_warns(runner, adir, monkeypatch):
    """模板外多余 kw=v → warnings 如实标，不静默吞。"""
    _write_adapter(adir)
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page,
                ['api', 'bili-search', 'keyword=x', 'zzz=1'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert any('zzz' in w for w in d['warnings'])


@pytest.mark.unit
def test_api_plain_url_still_works(runner, adir, monkeypatch):
    """非 adapter 名仍按 URL 处理（注册表未命中）。"""
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page,
                ['api', 'https://e.com/x'])
    assert r.exit_code == 0, r.output
    assert 'via' not in json.loads(r.output)['data']
    url, _ = _fetch_args(page.scripts[0])
    assert url == 'https://e.com/x'
    # URL 形态带 kw=v → 拒绝
    r = _invoke(runner, monkeypatch, page, ['api', 'https://e.com/x', 'a=1'])
    assert json.loads(r.output)['code'] == 'INVALID_ARGS'


@pytest.mark.unit
def test_api_adapter_post_uses_recorded_body(runner, adir, monkeypatch):
    """POST adapter：method/body 取录制值，-d 可覆盖。"""
    _write_adapter(adir, name='post-api', endpoint={
        'method': 'POST', 'url_template': 'https://x.com/l',
        'params_keys': [], 'body': '{"a":1}'})
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page, ['api', 'post-api'])
    _, opts = _fetch_args(page.scripts[0])
    assert opts['method'] == 'POST' and opts['body'] == '{"a":1}'
    page.scripts.clear()
    r = _invoke(runner, monkeypatch, page,
                ['api', 'post-api', '-d', '{"b":2}'])
    _, opts = _fetch_args(page.scripts[0])
    assert opts['body'] == '{"b":2}'


# ── fail-closed ui_fallback ─────────────────────────────────────

def _fb_adapter(adir, name='fb-api', **kw):
    return _write_adapter(adir, name=name, ui_fallback={
        'session': 's1', 'nav_url': 'https://nav.x/p', 'goal': '搜索'})


def _seq_invoke(calls, responses):
    def _i(argv):
        calls.append(list(argv))
        return responses.pop(0) if responses else (0, {'status': 'ok'})
    return _i


@pytest.mark.unit
def test_api_fetch_fail_triggers_ui_fallback(runner, adir, monkeypatch):
    """fetch reject → open nav_url→decide goal→done → via=ui_fallback。"""
    _fb_adapter(adir)
    page = FetchPage(exc=RuntimeError('Failed to fetch'))
    calls = []
    responses = [
        (0, {'status': 'ok'}),                                   # open
        (0, {'status': 'ok',
             'data': {'action': 'done', 'done': True}}),         # decide
    ]
    monkeypatch.setattr(batch_mod, '_invoke',
                        _seq_invoke(calls, responses))
    r = _invoke(runner, monkeypatch, page,
                ['api', 'fb-api', 'keyword=py'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert d['ok'] is True and d['via'] == 'ui_fallback'
    assert d['adapter'] == 'fb-api' and 'fetch' in d['fallback_reason']
    assert calls[0] == ['open', 'https://nav.x/p', '-S', 's1']
    assert calls[1] == ['decide', '搜索', '-S', 's1']


@pytest.mark.unit
def test_api_http_4xx_triggers_ui_fallback(runner, adir, monkeypatch):
    """http_status≥400 同样降级（fail-closed 覆盖非 2xx 失败）。"""
    _fb_adapter(adir)
    page = FetchPage(ret={'status': 500, 'ok': False,
                          'url': 'https://x.com', 'body': 'err'})
    calls = []
    responses = [(0, {'status': 'ok'}),
                 (0, {'status': 'ok',
                      'data': {'action': 'done', 'done': True}})]
    monkeypatch.setattr(batch_mod, '_invoke',
                        _seq_invoke(calls, responses))
    r = _invoke(runner, monkeypatch, page,
                ['api', 'fb-api', 'keyword=py'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert d['via'] == 'ui_fallback' and '500' in d['fallback_reason']


@pytest.mark.unit
def test_api_fallback_executes_decide_action(runner, adir, monkeypatch):
    """decide 返回 click ref → 执行 click ref:N 后再 decide 至 done。"""
    _fb_adapter(adir)
    page = FetchPage(ret={'status': 404, 'ok': False,
                          'url': 'https://x.com', 'body': 'nf'})
    calls = []
    responses = [
        (0, {'status': 'ok'}),                                   # open
        (0, {'status': 'ok',
             'data': {'action': 'click', 'ref': 3}}),            # decide
        (0, {'status': 'ok'}),                                   # click ref:3
        (0, {'status': 'ok',
             'data': {'action': 'done', 'done': True}}),         # decide
    ]
    monkeypatch.setattr(batch_mod, '_invoke',
                        _seq_invoke(calls, responses))
    r = _invoke(runner, monkeypatch, page,
                ['api', 'fb-api', 'keyword=py'])
    assert r.exit_code == 0, r.output
    assert ['click', 'ref:3', '-S', 's1'] in calls
    assert json.loads(r.output)['data']['via'] == 'ui_fallback'


@pytest.mark.unit
def test_api_fallback_chain_failure_is_error(runner, adir, monkeypatch):
    """回退链自身失败 → API_FETCH_FAILED 报错（fail-closed 不装成功）。"""
    _fb_adapter(adir)
    page = FetchPage(exc=RuntimeError('boom'))
    calls = []
    responses = [(0, {'status': 'ok'}),
                 (1, {'status': 'error', 'code': 'AGENT_UNSURE'})]
    monkeypatch.setattr(batch_mod, '_invoke',
                        _seq_invoke(calls, responses))
    r = _invoke(runner, monkeypatch, page,
                ['api', 'fb-api', 'keyword=py'])
    assert json.loads(r.output)['code'] == 'API_FETCH_FAILED'


@pytest.mark.unit
def test_api_fetch_fail_no_fallback_still_error(runner, adir, monkeypatch):
    """无 ui_fallback 的 adapter：fetch reject 仍按原契约报错。"""
    _write_adapter(adir, name='nofb')
    page = FetchPage(exc=RuntimeError('Failed to fetch'))
    calls = []
    monkeypatch.setattr(batch_mod, '_invoke',
                        _seq_invoke(calls, []))
    r = _invoke(runner, monkeypatch, page, ['api', 'nofb', 'keyword=x'])
    assert json.loads(r.output)['code'] == 'API_FETCH_FAILED'
    assert calls == []               # 未配置回退链，不走 _invoke


@pytest.mark.unit
def test_api_fallback_step_cap(runner, adir, monkeypatch):
    """decide 永不 done → 步数上限耗尽报错（防死循环）。"""
    _fb_adapter(adir)
    page = FetchPage(exc=RuntimeError('x'))
    calls = []
    responses = [(0, {'status': 'ok'})] + [
        (0, {'status': 'ok', 'data': {'action': 'wait'}})
        for _ in range(net_mod._UI_FB_MAX_STEPS + 2)]
    monkeypatch.setattr(batch_mod, '_invoke',
                        _seq_invoke(calls, responses))
    r = _invoke(runner, monkeypatch, page,
                ['api', 'fb-api', 'keyword=py'])
    assert json.loads(r.output)['code'] == 'API_FETCH_FAILED'


# ── serve {"action":"api"} 路由 ─────────────────────────────────

@pytest.mark.unit
def test_action_argv_name_params():
    argv, err = serve_mod._action_argv(
        {'action': 'api', 'name': 'bili-search',
         'params': {'keyword': 'py', 'p': 2}, 'session': 's1'})
    assert err is None
    assert argv == ['api', 'bili-search', 'keyword=py', 'p=2',
                    '-S', 's1']


@pytest.mark.unit
def test_action_argv_url_form():
    argv, err = serve_mod._action_argv(
        {'action': 'api', 'url': 'https://x.com/l', 'method': 'post',
         'headers': {'A': 'b'}, 'json': {'u': 1}, 'timeout': 7})
    assert err is None
    assert argv == ['api', 'https://x.com/l', '-X', 'post',
                    '-H', 'A: b', '--json', '{"u":1}',
                    '--timeout', '7']


@pytest.mark.unit
def test_action_argv_validation():
    assert serve_mod._action_argv({'action': 'api'})[1]
    assert serve_mod._action_argv(
        {'action': 'api', 'name': 'a', 'url': 'u'})[1]
    assert serve_mod._action_argv(
        {'action': 'api', 'name': 'a', 'params': 'x'})[1]
    assert serve_mod._action_argv(
        {'action': 'api', 'url': 'u', 'data': 'd', 'json': {}})[1]
    assert serve_mod._action_argv({'action': 'nope'})[1]


def _invoke_serve(stdin_lines, monkeypatch):
    argv_log = []
    monkeypatch.setattr(serve_mod, '_invoke',
                        lambda argv: (argv_log.append(list(argv)),
                                      (0, {'status': 'ok'}))[1])
    import io, sys
    monkeypatch.setattr(sys, 'stdin',
                        io.StringIO('\n'.join(stdin_lines) + '\n'))
    out = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', out)
    code = serve_mod._serve(session=None)
    lines = [json.loads(l) for l in out.getvalue().strip().splitlines()]
    return code, lines, argv_log


@pytest.mark.unit
def test_serve_action_api_routed(monkeypatch):
    """{"action":"api",...} 路由到 api 命令 argv。"""
    _, lines, argv_log = _invoke_serve(
        ['{"action":"api","name":"bili-search",'
         '"params":{"keyword":"py"},"session":"s1"}',
         '{"action":"api","url":"https://x.com","method":"POST"}',
         '{"action":"bogus"}'],
        monkeypatch)
    assert argv_log[0] == ['api', 'bili-search', 'keyword=py',
                           '-S', 's1']
    assert argv_log[1] == ['api', 'https://x.com', '-X', 'POST']
    assert lines[0]['exit'] == 0 and lines[1]['exit'] == 0
    assert lines[2]['exit'] == 2           # 未知 action → 参数错误
    assert 'action' in lines[2]['result']['message']


# ── 单元级：模板构建/插值 ───────────────────────────────────────

@pytest.mark.unit
def test_build_and_render_template_roundtrip():
    tpl, keys = net_mod._build_url_template(
        'https://a.com/s?kw=java&p=1', {'kw': 'keyword'})
    assert tpl == 'https://a.com/s?kw={keyword}&p=1'
    assert keys == ['keyword']
    url, missing = net_mod._render_template(tpl, {'keyword': 'go'})
    assert missing == [] and url == 'https://a.com/s?kw=go&p=1'
    url, missing = net_mod._render_template(tpl, {})
    assert missing == ['keyword']


@pytest.mark.unit
def test_adapter_name_validation():
    assert net_mod._adapter_path('good-name_1') is not None
    for bad in ('a/b', '..', 'a b', 'https://x.com', '', '-x'):
        assert net_mod._adapter_path(bad) is None, bad
