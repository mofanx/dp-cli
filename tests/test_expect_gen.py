# -*- coding:utf-8 -*-
"""expect-gen 命令 + locators/suggest.py 候选生成的单元测试。"""
import json

import pytest
from click.testing import CliRunner

import dp_cli.commands.assert_cmd as assert_mod
from dp_cli.locators.suggest import (pw_candidates, implicit_role,
                                     dp_loc_to_pw)
from dp_cli.locators import parse_pw, build_pw_count_js, build_pw_js

from tests.conftest import FakeEle, FakePage


@pytest.fixture
def runner():
    return CliRunner()


def _invoke(runner, monkeypatch, page, args):
    from dp_cli.main import cli
    monkeypatch.setattr(assert_mod, '_get_page', lambda s: page)
    return runner.invoke(cli, args)


def _out(result):
    return json.loads(result.output)


# ─────────────────────────────────────────────────────────────────────────────
# 页面级（无 --ele）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_expect_gen_page_only(runner, monkeypatch):
    page = FakePage(url='https://ex.com/dash', title='仪表盘')
    r = _invoke(runner, monkeypatch, page, ['expect-gen'])
    assert r.exit_code == 0
    d = _out(r)['data']
    assert 'to_have_url("https://ex.com/dash")' in d['script']
    assert 'to_have_title("仪表盘")' in d['script']
    assert d['count'] == 2
    # 断言依据注释
    assert '# 当前 URL' in d['script']


@pytest.mark.unit
def test_expect_gen_empty_page_skips(runner, monkeypatch):
    page = FakePage(url='', title='')
    r = _invoke(runner, monkeypatch, page, ['expect-gen'])
    assert r.exit_code == 0
    assert _out(r)['data']['count'] == 0


# ─────────────────────────────────────────────────────────────────────────────
# --ele 元素级
# ─────────────────────────────────────────────────────────────────────────────

def _patch_info(monkeypatch, info):
    monkeypatch.setattr(assert_mod, '_ele_info', lambda ele: info)


@pytest.mark.unit
def test_expect_gen_ele_role_name_priority(runner, monkeypatch):
    """role+accname → get_by_role 首选，优先级 > testid > text > css。"""
    info = {'tag': 'button', 'attrs': {'data-testid': 'login-btn'},
            'text': '登录', 'visible': True, 'accname': '登录', 'label': '',
            'value': '', 'checked': False, 'disabled': False}
    _patch_info(monkeypatch, info)
    page = FakePage(ele=FakeEle(raw_text='登录'), run_js_return=1)
    r = _invoke(runner, monkeypatch, page, ['expect-gen', '--ele', '#btn'])
    assert r.exit_code == 0
    d = _out(r)['data']
    chosen = d['target']['chosen']
    assert chosen['kind'] == 'role'
    assert 'get_by_role("button", name="登录")' in chosen['py']
    # 候选排序：role+name > testid > text > css
    kinds = [c['kind'] for c in d['target']['candidates']]
    assert kinds == ['role', 'testid', 'text', 'css']
    # 断言行：visible + contain_text + enabled + accname + count
    assert 'to_be_visible()' in d['script']
    assert 'to_contain_text("登录")' in d['script']
    assert 'to_be_enabled()' in d['script']
    assert 'to_have_accessible_name("登录")' in d['script']
    assert 'to_have_count(1)' in d['script']


@pytest.mark.unit
def test_expect_gen_ele_testid_when_no_name(runner, monkeypatch):
    """无 accname 时 testid 成为首选。"""
    info = {'tag': 'span', 'attrs': {'data-testid': 'price'},
            'text': '¥9.9', 'visible': True, 'accname': '', 'label': '',
            'value': '', 'checked': False, 'disabled': False}
    _patch_info(monkeypatch, info)
    page = FakePage(ele=FakeEle(), run_js_return=3)
    r = _invoke(runner, monkeypatch, page,
                ['expect-gen', '--ele', 'css:.price'])
    d = _out(r)['data']
    assert d['target']['chosen']['kind'] == 'testid'
    assert 'get_by_test_id("price")' in d['target']['chosen']['py']
    assert 'to_have_count(3)' in d['script']


@pytest.mark.unit
def test_expect_gen_ele_css_fallback(runner, monkeypatch):
    info = {'tag': 'div', 'attrs': {'id': 'app'},
            'text': '', 'visible': True, 'accname': '', 'label': '',
            'value': '', 'checked': False, 'disabled': False}
    _patch_info(monkeypatch, info)
    page = FakePage(ele=FakeEle(), run_js_return=1)
    r = _invoke(runner, monkeypatch, page, ['expect-gen', '--ele', '#app'])
    d = _out(r)['data']
    assert d['target']['chosen']['kind'] == 'css'
    assert 'page.locator("#app")' in d['script']


@pytest.mark.unit
def test_expect_gen_ele_input_value_checked(runner, monkeypatch):
    info = {'tag': 'input', 'input_type': 'checkbox',
            'attrs': {'name': 'agree'}, 'text': '', 'visible': True,
            'accname': '同意协议', 'label': '同意协议',
            'value': 'on', 'checked': True, 'disabled': False}
    _patch_info(monkeypatch, info)
    page = FakePage(ele=FakeEle(), run_js_return=1)
    r = _invoke(runner, monkeypatch, page, ['expect-gen', '--ele', '#agree'])
    d = _out(r)['data']
    assert 'to_be_checked()' in d['script']
    assert 'to_have_value("on")' in d['script']
    # checkbox 隐式 role + label
    kinds = [c['kind'] for c in d['target']['candidates']]
    assert kinds[0] == 'role' and 'label' in kinds


@pytest.mark.unit
def test_expect_gen_ele_hidden_and_count_fail(runner, monkeypatch):
    """不可见 → to_be_hidden；count 求值失败 → 不含 to_have_count。"""
    info = {'tag': 'div', 'attrs': {'id': 'x'},
            'text': '', 'visible': False, 'accname': '', 'label': '',
            'value': '', 'checked': False, 'disabled': False}
    _patch_info(monkeypatch, info)
    page = FakePage(ele=FakeEle(), run_js_return=RuntimeError('boom'))
    r = _invoke(runner, monkeypatch, page, ['expect-gen', '--ele', '#x'])
    assert r.exit_code == 0
    d = _out(r)['data']
    assert 'to_be_hidden()' in d['script']
    assert 'to_have_count' not in d['script']
    assert d['target']['matches'] is None


@pytest.mark.unit
def test_expect_gen_ele_no_count_flag(runner, monkeypatch):
    info = {'tag': 'button', 'attrs': {}, 'text': 'OK', 'visible': True,
            'accname': 'OK', 'label': '', 'value': '', 'checked': False,
            'disabled': False}
    _patch_info(monkeypatch, info)
    page = FakePage(ele=FakeEle(), run_js_return=5)
    r = _invoke(runner, monkeypatch, page,
                ['expect-gen', '--ele', '#ok', '--no-count'])
    d = _out(r)['data']
    assert 'to_have_count' not in d['script']


@pytest.mark.unit
def test_expect_gen_ele_missing(runner, monkeypatch):
    r = _invoke(runner, monkeypatch, FakePage(ele=None),
                ['expect-gen', '--ele', '#gone'])
    assert r.exit_code == 4
    assert _out(r)['code'] == 'ELEMENT_NOT_FOUND'


@pytest.mark.unit
def test_expect_gen_ele_and_all_conflict(runner, monkeypatch):
    r = _invoke(runner, monkeypatch, FakePage(),
                ['expect-gen', '--ele', '#x', '--all'])
    assert r.exit_code == 2
    assert _out(r)['code'] == 'INVALID_ARGS'


# ─────────────────────────────────────────────────────────────────────────────
# --all 枚举
# ─────────────────────────────────────────────────────────────────────────────

def _fake_snap(nodes, extras=None):
    tree = {'nodeId': 'r', 'role': 'WebArea', 'children': nodes}
    for i, n in enumerate(nodes):
        n.setdefault('nodeId', f'n{i}')
        n.setdefault('children', [])
    return {'page': {'url': 'u', 'title': 't'}, 'tree': tree,
            'clickable_extras': extras or []}


@pytest.mark.unit
def test_expect_gen_all_enumerates(runner, monkeypatch):
    nodes = [
        {'role': 'button', 'name': '登录', 'locator': '#l'},
        {'role': 'link', 'name': '帮助', 'locator': 'text:帮助'},
        {'role': 'StaticText', 'name': '不可断言'},
    ]
    monkeypatch.setattr('dp_cli.snapshot.a11y.take_a11y_snapshot',
                        lambda p, **kw: _fake_snap(nodes))
    r = _invoke(runner, monkeypatch, FakePage(), ['expect-gen', '--all'])
    assert r.exit_code == 0
    d = _out(r)['data']
    assert d['total_assertable'] == 2
    codes = [e['code'] for e in d['expects']]
    assert 'expect(page.get_by_role("button", name="登录")).to_be_visible()' in codes
    assert 'expect(page.get_by_role("link", name="帮助")).to_contain_text("帮助")' in codes


@pytest.mark.unit
def test_expect_gen_all_limit_and_extras(runner, monkeypatch):
    nodes = [{'role': 'button', 'name': f'b{i}'} for i in range(5)]
    extras = [{'tag': 'div', 'text': '自定义', 'locator': 'text:自定义'}]
    monkeypatch.setattr('dp_cli.snapshot.a11y.take_a11y_snapshot',
                        lambda p, **kw: _fake_snap(nodes, extras))
    r = _invoke(runner, monkeypatch, FakePage(),
                ['expect-gen', '--all', '--limit', '3'])
    d = _out(r)['data']
    assert len(d['elements']) == 3
    assert d['total_assertable'] == 6
    assert d['truncated'] is True


@pytest.mark.unit
def test_expect_gen_all_extra_dp_locator(runner, monkeypatch):
    """clickable 补充元素经 dp_loc_to_pw 转换后产出断言。"""
    extras = [{'tag': 'span', 'text': '更多', 'locator': 'text:更多'}]
    monkeypatch.setattr('dp_cli.snapshot.a11y.take_a11y_snapshot',
                        lambda p, **kw: _fake_snap([], extras))
    r = _invoke(runner, monkeypatch, FakePage(), ['expect-gen', '--all'])
    d = _out(r)['data']
    assert any('get_by_text("更多")' in e['code'] for e in d['expects'])


# ─────────────────────────────────────────────────────────────────────────────
# suggest.py 纯函数单元
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_implicit_role_map():
    assert implicit_role('a', '', {'href': '/x'}) == 'link'
    assert implicit_role('a') is None                    # 无 href 不算 link
    assert implicit_role('input', 'checkbox') == 'checkbox'
    assert implicit_role('input', 'email') == 'textbox'
    assert implicit_role('button') == 'button'
    assert implicit_role('div', '', {'role': 'tab'}) == 'tab'  # 显式优先
    assert implicit_role('form') is None                 # 无 label 无 landmark


@pytest.mark.unit
def test_pw_candidates_ordering():
    info = {'tag': 'input', 'input_type': 'text',
            'attrs': {'data-testid': 'uname', 'placeholder': '用户名'},
            'text': '', 'accname': '用户名', 'label': '用户名'}
    kinds = [c['kind'] for c in pw_candidates(info)]
    assert kinds == ['role', 'testid', 'label', 'placeholder', 'css']


@pytest.mark.unit
def test_pw_candidates_long_text_substring():
    info = {'tag': 'p', 'attrs': {}, 'text': 'x' * 100, 'accname': '',
            'label': ''}
    cands = pw_candidates(info)
    text_c = [c for c in cands if c['kind'] == 'text'][0]
    assert 'exact=True' not in text_c['py']     # 长文本用子串
    assert 'css' in [c['kind'] for c in cands]


@pytest.mark.unit
def test_dp_loc_to_pw():
    assert dp_loc_to_pw('#a')['py'] == 'page.locator("#a")'
    assert dp_loc_to_pw('@data-testid=x')['py'] == \
        'page.locator("[data-testid=\\"x\\"]")'
    assert dp_loc_to_pw('text:登录')['py'] == 'page.get_by_text("登录")'
    assert dp_loc_to_pw('xpath://div')['py'] == \
        'page.locator("xpath=//div")'
    assert dp_loc_to_pw('css:.c')['expr'] == 'css=.c'
    assert dp_loc_to_pw('pw:role=button')['expr'] == 'role=button'
    assert dp_loc_to_pw('') is None


# ─────────────────────────────────────────────────────────────────────────────
# build_pw_count_js
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_build_pw_count_js_tail():
    matchers = parse_pw('role=button[name="x"]')
    js = build_pw_count_js(matchers)
    assert 'return scope.length;' in js
    assert 'data-dp-ref' not in js          # 计数模式不打标
    assert 'JSON.parse' in js               # 核心求值逻辑复用


@pytest.mark.unit
def test_build_pw_js_unchanged():
    matchers = parse_pw('text="a"')
    js = build_pw_js(matchers)
    assert 'data-dp-ref' in js and 'return marker;' in js


@pytest.mark.unit
def test_pw_candidates_non_default_testid_falls_back_to_css():
    """data-qa/data-cy/data-test（非 data-testid）→ page.locator('[attr=...]')
    而非 get_by_test_id（后者默认只查 data-testid）。"""
    from dp_cli.locators.suggest import pw_candidates
    for attr in ('data-qa', 'data-cy', 'data-test'):
        info = {'tag': 'button', 'attrs': {attr: 'save-btn'}, 'text': ''}
        cands = pw_candidates(info)
        testid = next((c for c in cands if c['kind'] == 'testid'), None)
        assert testid is not None
        assert 'get_by_test_id' not in testid['py']
        assert testid['py'].startswith('page.locator(')
        assert attr in testid['py']


@pytest.mark.unit
def test_pw_candidates_non_default_testid_expr_resolvable():
    """data-qa/data-cy 的 expr 必须走 css=（pw 引擎 testid 不查
    这两个 attr），保证 expr 可回喂 dp 命令验证。"""
    from dp_cli.locators.suggest import pw_candidates
    for attr in ('data-qa', 'data-cy'):
        info = {'tag': 'button', 'attrs': {attr: 'x'}, 'text': ''}
        testid = next(c for c in pw_candidates(info)
                      if c['kind'] == 'testid')
        assert testid['expr'].startswith('css=')
        parse_pw(testid['expr'])   # 必须是 pw 引擎可解析的表达式
    # data-test / data-test-id 引擎可查 → 仍用 testid= expr
    for attr in ('data-test', 'data-test-id', 'data-testid'):
        info = {'tag': 'button', 'attrs': {attr: 'x'}, 'text': ''}
        testid = next(c for c in pw_candidates(info)
                      if c['kind'] == 'testid')
        assert testid['expr'].startswith('testid=')
        parse_pw(testid['expr'])


@pytest.mark.unit
def test_pw_candidates_data_testid_uses_get_by_test_id():
    """data-testid → get_by_test_id（Playwright 默认 attr）。"""
    from dp_cli.locators.suggest import pw_candidates
    info = {'tag': 'button', 'attrs': {'data-testid': 'x'}, 'text': ''}
    cands = pw_candidates(info)
    testid = next(c for c in cands if c['kind'] == 'testid')
    assert 'get_by_test_id' in testid['py']
