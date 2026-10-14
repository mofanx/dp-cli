# -*- coding:utf-8 -*-
"""--fields/--head 输出裁剪（listen-stop/console-dump/hook-dump/cookie-list）回归测试"""
import json
from collections import deque

import pytest
from click.testing import CliRunner

from dp_cli.main import cli
import dp_cli.commands.hook as hook_mod
import dp_cli.commands.network as net_mod
import dp_cli.commands.page as page_mod
import dp_cli.commands.storage as storage_mod
from dp_cli.commands._utils import shape_records


runner = CliRunner()


def _out(r):
    return json.loads(r.output)


def _patch(mod, monkeypatch, page, sess=None):
    monkeypatch.setattr(mod, '_get_page', lambda s: page)
    if hasattr(mod, 'load_session'):
        monkeypatch.setattr(mod, 'load_session', lambda s: dict(sess or {}))


# ── shape_records 单元级 ────────────────────────────────────────────────

@pytest.mark.unit
def test_shape_records_defaults():
    """无 fields/head：记录原样返回，total=总数。"""
    recs = [{'a': 1, 'b': 2}, {'a': 3, 'b': 4}]
    shaped, total = shape_records(recs, None, None, {'a', 'b'})
    assert shaped == recs and total == 2


@pytest.mark.unit
def test_shape_records_fields_whitelist():
    """--fields 投影：只保留白名单键，缺键→None。"""
    recs = [{'a': 1, 'b': 2}]
    shaped, _ = shape_records(recs, 'a,c', None, {'a', 'b', 'c'})
    assert shaped == [{'a': 1, 'c': None}]


@pytest.mark.unit
def test_shape_records_invalid_field():
    """非法字段名 → INVALID_ARGS 退出 2（不静默丢）。"""
    with pytest.raises(SystemExit):
        shape_records([{'a': 1}], 'a,bad', None, {'a'})


@pytest.mark.unit
def test_shape_records_head_truncates():
    """--head 截断，total 保留原总数。"""
    recs = [{'a': i} for i in range(5)]
    shaped, total = shape_records(recs, None, 2, {'a'})
    assert len(shaped) == 2 and total == 5


@pytest.mark.unit
def test_shape_records_head_negative():
    """--head -1 → INVALID_ARGS。"""
    with pytest.raises(SystemExit):
        shape_records([{'a': 1}], None, -1, {'a'})


# ── hook-dump ───────────────────────────────────────────────────────────

class _HookPage:
    def __init__(self, events):
        self._events = events

    def run_cdp(self, cmd, **kw):
        return {'result': {'value': self._events}}


@pytest.mark.unit
def test_hook_dump_fields_head(monkeypatch):
    """hook-dump --fields 投影 + --head 截断 + total。"""
    events = [{'seq': i, 'ts': 1, 'hook': 'h1', 'kind': 'set',
               'data': {'name': 'x'}} for i in range(3)]
    _patch(hook_mod, monkeypatch, _HookPage(events))
    r = runner.invoke(cli, ['hook-dump', '--fields', 'seq,kind', '--head', '2'])
    assert r.exit_code == 0, r.output
    d = _out(r)['data']
    assert d['count'] == 2 and d['total'] == 3
    assert d['events'] == [{'seq': 0, 'kind': 'set'},
                           {'seq': 1, 'kind': 'set'}]


@pytest.mark.unit
def test_hook_dump_bad_field(monkeypatch):
    """hook-dump 非法 --fields → INVALID_ARGS。"""
    _patch(hook_mod, monkeypatch, _HookPage([]))
    r = runner.invoke(cli, ['hook-dump', '--fields', 'seq,nope'])
    assert r.exit_code == 2
    assert 'nope' in _out(r)['message']


# ── console-dump ────────────────────────────────────────────────────────

class _Driver:
    def set_callback(self, *a):
        pass


class _ConsolePage:
    def __init__(self):
        self._dp_console_msgs = deque([
            {'level': 'log', 'text': f'm{i}', 'source': 'console',
             'timestamp': i} for i in range(4)])
        self.driver = _Driver()


@pytest.mark.unit
def test_console_dump_trim(monkeypatch):
    """console-dump --fields/--head：裁剪后仍保留 keep/clear 语义。"""
    _patch(page_mod, monkeypatch, _ConsolePage())
    r = runner.invoke(cli, ['console-dump', '--fields', 'level,text',
                            '--head', '1'])
    assert r.exit_code == 0, r.output
    d = _out(r)['data']
    assert d['count'] == 1 and d['total'] == 4
    assert d['messages'] == [{'level': 'log', 'text': 'm0'}]


# ── cookie-list ─────────────────────────────────────────────────────────

class _CookiePage:
    def cookies(self, all_domains=True, all_info=True):
        return [{'name': f'c{i}', 'value': str(i), 'domain': '.ex.com',
                 'path': '/', 'secure': True, 'httpOnly': False,
                 'sameSite': 'Lax', 'expires': 999} for i in range(3)]


@pytest.mark.unit
def test_cookie_list_trim(monkeypatch):
    """cookie-list --fields name,value --head 2。"""
    _patch(storage_mod, monkeypatch, _CookiePage())
    r = runner.invoke(cli, ['cookie-list', '--fields', 'name,value',
                            '--head', '2'])
    assert r.exit_code == 0, r.output
    d = _out(r)['data']
    assert d['count'] == 2 and d['total'] == 3
    assert d['cookies'][0] == {'name': 'c0', 'value': '0'}


@pytest.mark.unit
def test_cookie_list_bad_field(monkeypatch):
    """cookie-list 非法字段报错列出可用字段。"""
    _patch(storage_mod, monkeypatch, _CookiePage())
    r = runner.invoke(cli, ['cookie-list', '--fields', 'name,bogus'])
    assert r.exit_code == 2
    assert 'bogus' in _out(r)['message'] and 'domain' in _out(r)['message']


# ── listen-stop ─────────────────────────────────────────────────────────

class _Resp:
    status = 200
    headers = {'x': 'y'}
    body = 'B' * 100


class _Req:
    headers = {'a': 'b'}
    postData = 'p'


class _Pkt:
    def __init__(self, i):
        self.url = f'http://x/{i}'
        self.method = 'POST'
        self.response = _Resp()
        self.resourceType = 'xhr'
        self.request = _Req()


class _Listen:
    def wait(self, **kw):
        return [_Pkt(i) for i in range(3)]

    def stop(self):
        pass


class _NetPage:
    listen = _Listen()


@pytest.mark.unit
def test_listen_stop_trim(monkeypatch):
    """listen-stop --fields url,status --head 1：重字段裁剪省 token。"""
    _patch(net_mod, monkeypatch, _NetPage())
    r = runner.invoke(cli, ['listen-stop', '--fields', 'url,status',
                            '--head', '1'])
    assert r.exit_code == 0, r.output
    d = _out(r)['data']
    assert d['count'] == 1 and d['total'] == 3
    assert d['packets'] == [{'url': 'http://x/0', 'status': 200}]


@pytest.mark.unit
def test_listen_stop_default_unchanged(monkeypatch):
    """不带裁剪参数：字段与数量与改动前完全一致。"""
    _patch(net_mod, monkeypatch, _NetPage())
    r = runner.invoke(cli, ['listen-stop'])
    assert r.exit_code == 0, r.output
    d = _out(r)['data']
    assert d['count'] == 3 and 'total' not in d
    pkt = d['packets'][0]
    assert 'request_headers' in pkt and 'response_body' in pkt
