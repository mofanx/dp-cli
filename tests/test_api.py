# -*- coding:utf-8 -*-
"""dpx api（页内 fetch 原语）测试。

mock _get_page → 假 page 捕获 run_js 脚本，断言 URL/method/headers/body
透传、--json 序列化、错误路径、--raw/--output/截断分支。
全程不依赖真实浏览器/网络。
"""
import json
import re

import pytest
from click.testing import CliRunner

import dp_cli.commands.network as net_mod


class FetchPage:
    """捕获 run_js 脚本并按预设返回 fetch 结果。"""

    def __init__(self, ret=None, exc=None):
        self.scripts = []
        self.timeouts = []
        self._ret = ret if ret is not None else {
            'status': 200, 'ok': True,
            'url': 'https://example.com/api', 'body': '{"a":1}'}
        self._exc = exc

    def run_js(self, script, *a, **k):
        self.scripts.append(script)
        self.timeouts.append(k.get('timeout'))
        if self._exc is not None:
            raise self._exc
        return self._ret


def _fetch_args(script):
    """从生成脚本提取 fetch(url, opts) 的两个 JSON 实参。"""
    m = re.match(r'return fetch\((\{.*?\}|".*?"),(\{.*\})\)\.then',
                 script)
    assert m, f'脚本形状不符: {script}'
    return json.loads(m.group(1)), json.loads(m.group(2))


@pytest.fixture
def runner():
    return CliRunner()


def _invoke(runner, monkeypatch, page, argv):
    monkeypatch.setattr(net_mod, '_get_page', lambda s: page)
    from dp_cli.main import cli
    return runner.invoke(cli, argv)


# ── URL/method/headers/body 透传 ─────────────────────────────────────

@pytest.mark.unit
def test_api_get_default(runner, monkeypatch):
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page,
                ['api', 'https://example.com/api'])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out['status'] == 'ok'
    assert out['data']['http_status'] == 200
    assert out['data']['body'] == '{"a":1}'
    url, opts = _fetch_args(page.scripts[0])
    assert url == 'https://example.com/api'
    assert opts['method'] == 'GET'
    assert 'body' not in opts and 'headers' not in opts
    assert page.timeouts[0] == 30


@pytest.mark.unit
def test_api_method_headers_body_passthrough(runner, monkeypatch):
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page, [
        'api', 'https://x.com/p', '-X', 'post',
        '-H', 'X-Token: abc', '-H', 'Accept: application/json',
        '-d', 'a=1&b=2'])
    assert r.exit_code == 0, r.output
    _, opts = _fetch_args(page.scripts[0])
    assert opts['method'] == 'POST'          # 归一化大写
    assert opts['headers'] == {'X-Token': 'abc',
                               'Accept': 'application/json'}
    assert opts['body'] == 'a=1&b=2'


@pytest.mark.unit
def test_api_relative_url_passthrough(runner, monkeypatch):
    """相对路径原样进 fetch——页内按 location 同源解析。"""
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page, ['api', '/api/me'])
    assert r.exit_code == 0, r.output
    url, _ = _fetch_args(page.scripts[0])
    assert url == '/api/me'


@pytest.mark.unit
def test_api_timeout_flag(runner, monkeypatch):
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page,
                ['api', '/x', '--timeout', '7'])
    assert r.exit_code == 0, r.output
    assert page.timeouts[0] == 7


# ── --json 序列化 ────────────────────────────────────────────────────

@pytest.mark.unit
def test_api_json_body_serialized(runner, monkeypatch):
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page, [
        'api', '/login', '-X', 'POST',
        '--json', '{ "u" : "admin" , "n" : 1 }'])
    assert r.exit_code == 0, r.output
    _, opts = _fetch_args(page.scripts[0])
    # 紧凑序列化（等价 JSON.stringify 无空格输出）
    assert opts['body'] == '{"u":"admin","n":1}'
    assert opts['headers']['Content-Type'] == 'application/json'


@pytest.mark.unit
def test_api_json_explicit_content_type_wins(runner, monkeypatch):
    """-H 显式 Content-Type 优先于 --json 默认值。"""
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page, [
        'api', '/x', '-X', 'POST', '--json', '{}',
        '-H', 'Content-Type: application/vnd.api+json'])
    assert r.exit_code == 0, r.output
    _, opts = _fetch_args(page.scripts[0])
    assert opts['headers']['Content-Type'] == 'application/vnd.api+json'


@pytest.mark.unit
def test_api_json_invalid(runner, monkeypatch):
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page,
                ['api', '/x', '-X', 'POST', '--json', '{bad'])
    assert r.exit_code == 2
    assert json.loads(r.output)['code'] == 'INVALID_JSON'
    assert not page.scripts


# ── 参数守卫 ─────────────────────────────────────────────────────────

@pytest.mark.unit
def test_api_data_json_mutex(runner, monkeypatch):
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page,
                ['api', '/x', '-X', 'POST', '-d', 'a', '--json', '{}'])
    assert r.exit_code == 2
    assert json.loads(r.output)['code'] == 'CONFLICTING_OPTIONS'


@pytest.mark.unit
def test_api_get_with_body_rejected(runner, monkeypatch):
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page,
                ['api', '/x', '-d', 'a=1'])
    assert r.exit_code == 2
    assert json.loads(r.output)['code'] == 'INVALID_ARGS'
    assert not page.scripts


@pytest.mark.unit
def test_api_bad_header(runner, monkeypatch):
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page,
                ['api', '/x', '-H', 'NoColon'])
    assert r.exit_code == 2
    assert json.loads(r.output)['code'] == 'INVALID_ARGS'


@pytest.mark.unit
def test_api_header_value_with_colon(runner, monkeypatch):
    """header value 含冒号（如 URL）按第一个冒号切。"""
    page = FetchPage()
    r = _invoke(runner, monkeypatch, page,
                ['api', '/x', '-H', 'Referer: http://a:1/p'])
    assert r.exit_code == 0, r.output
    _, opts = _fetch_args(page.scripts[0])
    assert opts['headers'] == {'Referer': 'http://a:1/p'}


# ── 状态码契约：fetch 成功即 ok ──────────────────────────────────────

@pytest.mark.unit
def test_api_non_2xx_is_data_not_error(runner, monkeypatch):
    """404/500 如实返回 data.http_status + ok=False——非 hard error。"""
    page = FetchPage(ret={'status': 404, 'ok': False,
                          'url': 'https://x.com/miss', 'body': 'nf'})
    r = _invoke(runner, monkeypatch, page, ['api', '/miss'])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out['status'] == 'ok'
    assert out['data']['http_status'] == 404
    assert out['data']['ok'] is False


@pytest.mark.unit
def test_api_fetch_rejected(runner, monkeypatch):
    """fetch reject（CORS/网络）→ API_FETCH_FAILED。"""
    page = FetchPage(exc=RuntimeError('TypeError: Failed to fetch'))
    r = _invoke(runner, monkeypatch, page, ['api', '/x'])
    assert r.exit_code == 3
    out = json.loads(r.output)
    assert out['status'] == 'error'
    assert out['code'] == 'API_FETCH_FAILED'


@pytest.mark.unit
def test_api_fetch_timeout(runner, monkeypatch):
    page = FetchPage(exc=TimeoutError('js timeout'))
    r = _invoke(runner, monkeypatch, page, ['api', '/x'])
    assert r.exit_code == 5
    assert json.loads(r.output)['code'] == 'WAIT_TIMEOUT'


@pytest.mark.unit
def test_api_malformed_result(runner, monkeypatch):
    page = FetchPage(ret='oops')
    r = _invoke(runner, monkeypatch, page, ['api', '/x'])
    assert r.exit_code == 3
    assert json.loads(r.output)['code'] == 'API_FETCH_FAILED'


# ── body 截断 / --raw / --output ────────────────────────────────────

@pytest.mark.unit
def test_api_body_truncated_at_64k(runner, monkeypatch):
    big = 'x' * (net_mod._API_BODY_LIMIT + 10)
    page = FetchPage(ret={'status': 200, 'ok': True,
                          'url': '/big', 'body': big})
    r = _invoke(runner, monkeypatch, page, ['api', '/big'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert d['truncated'] is True
    assert len(d['body']) == net_mod._API_BODY_LIMIT
    assert d['body_length'] == len(big)


@pytest.mark.unit
def test_api_raw_no_truncation(runner, monkeypatch):
    big = 'y' * (net_mod._API_BODY_LIMIT + 10)
    page = FetchPage(ret={'status': 200, 'ok': True,
                          'url': '/big', 'body': big})
    r = _invoke(runner, monkeypatch, page, ['api', '/big', '--raw'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert 'truncated' not in d
    assert d['body'] == big


@pytest.mark.unit
def test_api_output_writes_file(runner, monkeypatch, tmp_path):
    page = FetchPage(ret={'status': 200, 'ok': True,
                          'url': '/dl', 'body': 'file-body'})
    f = tmp_path / 'resp.txt'
    r = _invoke(runner, monkeypatch, page,
                ['api', '/dl', '--output', str(f)])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert d['saved_to'] == str(f)
    assert d['body_length'] == 9
    assert 'body' not in d
    assert f.read_text(encoding='utf-8') == 'file-body'


@pytest.mark.unit
def test_api_empty_body_no_crash(runner, monkeypatch):
    page = FetchPage(ret={'status': 204, 'ok': True,
                          'url': '/n', 'body': None})
    r = _invoke(runner, monkeypatch, page, ['api', '/n'])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)['data']['body'] == ''
