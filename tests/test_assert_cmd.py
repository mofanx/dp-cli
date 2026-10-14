# -*- coding:utf-8 -*-
"""
assert_cmd.py（assert-text/url/visible/count）与 page.py wait --js/--response
的单元测试。全程不依赖真实浏览器。
"""
import json

import pytest
from click.testing import CliRunner

import dp_cli.commands.assert_cmd as assert_mod
import dp_cli.commands.page as page_mod


# ─────────────────────────────────────────────────────────────────────────────
# 假对象
# ─────────────────────────────────────────────────────────────────────────────

from tests.conftest import FakeEle, FakeNoneEle as NoneEle, FakePage


class _FakeListen:
    def __init__(self, packets=None, start_error=None):
        self._packets = packets
        self._start_error = start_error
        self.started_with = []
        self.stopped = False

    def start(self, targets=None, **kw):
        if self._start_error:
            raise self._start_error
        self.started_with.append(targets)

    def wait(self, count, timeout, fit_count):
        return self._packets

    def stop(self):
        self.stopped = True


class _Pkt:
    def __init__(self, url='https://api.example.com/login', method='POST', status=200):
        self.url = url
        self.method = method

        class _Resp:
            pass
        self.response = _Resp()
        self.response.status = status


@pytest.fixture
def runner():
    return CliRunner()


def _invoke_assert(runner, monkeypatch, page, args):
    """打 assert_cmd._get_page patch 后调用命令。"""
    from dp_cli.main import cli
    monkeypatch.setattr(assert_mod, '_get_page', lambda s: page)
    return runner.invoke(cli, args)


def _invoke_wait(runner, monkeypatch, page, args):
    from dp_cli.main import cli
    monkeypatch.setattr(page_mod, '_get_page', lambda s: page)
    return runner.invoke(cli, ['wait'] + args)


# ─────────────────────────────────────────────────────────────────────────────
# assert-text
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_assert_text_page_pass(runner, monkeypatch):
    result = _invoke_assert(runner, monkeypatch,
                            FakePage(html='<html>登录成功，欢迎回来</html>'),
                            ['assert-text', '登录成功'])
    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out['status'] == 'ok'


@pytest.mark.unit
def test_assert_text_page_fail(runner, monkeypatch):
    result = _invoke_assert(runner, monkeypatch,
                            FakePage(html='<html>首页</html>'),
                            ['assert-text', '登录成功'])
    assert result.exit_code == 4
    out = json.loads(result.output)
    assert out['code'] == 'ASSERT_FAILED'


@pytest.mark.unit
def test_assert_text_locator_pass(runner, monkeypatch):
    page = FakePage(ele=FakeEle(raw_text='订单已提交'))
    result = _invoke_assert(runner, monkeypatch, page,
                            ['assert-text', '已提交', '--locator', '#msg'])
    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out['status'] == 'ok'


@pytest.mark.unit
def test_assert_text_locator_exact(runner, monkeypatch):
    page = FakePage(ele=FakeEle(raw_text='Hello'))
    ok_r = _invoke_assert(runner, monkeypatch, page,
                          ['assert-text', 'Hello', '--locator', '#t', '--exact'])
    assert ok_r.exit_code == 0
    bad_r = _invoke_assert(runner, monkeypatch, page,
                           ['assert-text', 'Hell', '--locator', '#t', '--exact'])
    assert bad_r.exit_code == 4


@pytest.mark.unit
def test_assert_text_locator_missing_fails(runner, monkeypatch):
    result = _invoke_assert(runner, monkeypatch, FakePage(ele=None),
                            ['assert-text', 'x', '--locator', '#gone'])
    assert result.exit_code == 4
    out = json.loads(result.output)
    assert out['code'] == 'ASSERT_FAILED'
    assert '元素不存在' in out['message']


# ─────────────────────────────────────────────────────────────────────────────
# assert-url
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_assert_url_contains(runner, monkeypatch):
    page = FakePage(url='https://example.com/dashboard?id=1')
    result = _invoke_assert(runner, monkeypatch, page, ['assert-url', 'dashboard'])
    assert result.exit_code == 0


@pytest.mark.unit
def test_assert_url_exact(runner, monkeypatch):
    page = FakePage(url='https://example.com/home')
    ok_r = _invoke_assert(runner, monkeypatch, page,
                          ['assert-url', 'https://example.com/home', '--exact'])
    assert ok_r.exit_code == 0
    bad_r = _invoke_assert(runner, monkeypatch, page,
                           ['assert-url', 'example.com/home', '--exact'])
    assert bad_r.exit_code == 4


@pytest.mark.unit
def test_assert_url_title_flag(runner, monkeypatch):
    page = FakePage(title='搜索结果 - demo')
    result = _invoke_assert(runner, monkeypatch, page,
                            ['assert-url', '搜索结果', '--title'])
    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out['data']['field'] == 'title'


# ─────────────────────────────────────────────────────────────────────────────
# assert-visible
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_assert_visible_pass(runner, monkeypatch):
    page = FakePage(ele=FakeEle(displayed=True))
    result = _invoke_assert(runner, monkeypatch, page,
                            ['assert-visible', '#result'])
    assert result.exit_code == 0


@pytest.mark.unit
def test_assert_visible_missing_fails(runner, monkeypatch):
    result = _invoke_assert(runner, monkeypatch, FakePage(ele=None),
                            ['assert-visible', '#gone'])
    assert result.exit_code == 4
    assert '元素不存在' in json.loads(result.output)['message']


@pytest.mark.unit
def test_assert_visible_hidden_element_fails(runner, monkeypatch):
    page = FakePage(ele=FakeEle(displayed=False))
    result = _invoke_assert(runner, monkeypatch, page,
                            ['assert-visible', '#x'])
    assert result.exit_code == 4
    assert '不可见' in json.loads(result.output)['message']


@pytest.mark.unit
def test_assert_visible_hidden_flag(runner, monkeypatch):
    # --hidden：元素不存在 → 通过；元素可见 → 失败
    r1 = _invoke_assert(runner, monkeypatch, FakePage(ele=None),
                        ['assert-visible', '.loading', '--hidden'])
    assert r1.exit_code == 0
    r2 = _invoke_assert(runner, monkeypatch,
                        FakePage(ele=FakeEle(displayed=True)),
                        ['assert-visible', '.loading', '--hidden'])
    assert r2.exit_code == 4


# ─────────────────────────────────────────────────────────────────────────────
# assert-count
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_assert_count_exact(runner, monkeypatch):
    page = FakePage(eles=[FakeEle(), FakeEle(), FakeEle()])
    ok_r = _invoke_assert(runner, monkeypatch, page, ['assert-count', '.item', '3'])
    assert ok_r.exit_code == 0
    assert json.loads(ok_r.output)['data']['count'] == 3
    bad_r = _invoke_assert(runner, monkeypatch, page, ['assert-count', '.item', '5'])
    assert bad_r.exit_code == 4


@pytest.mark.unit
def test_assert_count_min_max(runner, monkeypatch):
    page = FakePage(eles=[FakeEle()] * 4)
    ok_r = _invoke_assert(runner, monkeypatch, page,
                          ['assert-count', '.item', '--min', '1', '--max', '10'])
    assert ok_r.exit_code == 0
    bad_r = _invoke_assert(runner, monkeypatch, page,
                           ['assert-count', '.item', '--min', '5'])
    assert bad_r.exit_code == 4


@pytest.mark.unit
def test_assert_count_no_args_invalid(runner, monkeypatch):
    result = _invoke_assert(runner, monkeypatch, FakePage(),
                            ['assert-count', '.item'])
    assert result.exit_code == 2
    assert json.loads(result.output)['code'] == 'INVALID_ARGS'


@pytest.mark.unit
def test_assert_count_expected_and_range_conflict(runner, monkeypatch):
    """expected 与 --min/--max 同时给出应按声明互斥返回 INVALID_ARGS。"""
    for extra in (['--min', '1'], ['--max', '5'], ['--min', '1', '--max', '5']):
        result = _invoke_assert(runner, monkeypatch, FakePage(),
                                ['assert-count', '.item', '3'] + extra)
        assert result.exit_code == 2
        assert json.loads(result.output)['code'] == 'INVALID_ARGS'


@pytest.mark.unit
def test_assert_count_zero(runner, monkeypatch):
    result = _invoke_assert(runner, monkeypatch, FakePage(eles=[]),
                            ['assert-count', '.error', '0'])
    assert result.exit_code == 0


# ─────────────────────────────────────────────────────────────────────────────
# wait --js / --response
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_wait_js_truthy(runner, monkeypatch):
    page = FakePage(run_js_return=True)
    result = _invoke_wait(runner, monkeypatch, page,
                          ['--js', 'window.appReady === true'])
    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out['status'] == 'ok'
    assert out['data']['result'] is True


@pytest.mark.unit
def test_wait_js_timeout(runner, monkeypatch):
    page = FakePage(run_js_return=False)
    result = _invoke_wait(runner, monkeypatch, page,
                          ['--js', 'window.appReady', '--timeout', '1'])
    assert result.exit_code == 5
    assert json.loads(result.output)['code'] == 'WAIT_TIMEOUT'


@pytest.mark.unit
def test_wait_js_exception_keeps_polling(runner, monkeypatch):
    page = FakePage(run_js_return=RuntimeError('not ready'))
    result = _invoke_wait(runner, monkeypatch, page,
                          ['--js', 'x', '--timeout', '1'])
    assert result.exit_code == 5
    assert json.loads(result.output)['code'] == 'WAIT_TIMEOUT'


@pytest.mark.unit
def test_wait_response_match(runner, monkeypatch):
    listen = _FakeListen(packets=_Pkt())
    page = FakePage(listen=listen)
    result = _invoke_wait(runner, monkeypatch, page,
                          ['--response', 'api/login', '--timeout', '5'])
    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out['data']['url'] == 'https://api.example.com/login'
    assert out['data']['status'] == 200
    assert listen.started_with == ['api/login']
    assert listen.stopped


@pytest.mark.unit
def test_wait_response_timeout_stops_listen(runner, monkeypatch):
    listen = _FakeListen(packets=None)
    page = FakePage(listen=listen)
    result = _invoke_wait(runner, monkeypatch, page,
                          ['--response', 'api/x', '--timeout', '1'])
    assert result.exit_code == 5
    assert json.loads(result.output)['code'] == 'WAIT_TIMEOUT'
    assert listen.stopped


@pytest.mark.unit
def test_wait_response_existing_listener_not_stopped(runner, monkeypatch):
    """监听器已在运行时（start 抛错）只等待、不调用 stop。"""
    listen = _FakeListen(packets=_Pkt(), start_error=RuntimeError('already'))
    page = FakePage(listen=listen)
    result = _invoke_wait(runner, monkeypatch, page,
                          ['--response', 'api/login', '--timeout', '5'])
    assert result.exit_code == 0
    assert not listen.stopped
