# -*- coding:utf-8 -*-
"""TypeScript Playwright 导出：pw_ts 转换器 + record-export ts 格式
+ expect-gen --lang ts 的单元测试。"""
import json

import pytest
from click.testing import CliRunner

import dp_cli.commands.assert_cmd as assert_mod
from dp_cli.pw_ts import py_loc_to_ts, py_expect_to_ts
from dp_cli.recorder import export_actions

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
# py_loc_to_ts 定位器转换
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_py_loc_to_ts_role_with_name():
    """kwargs → options 对象。"""
    ts = py_loc_to_ts("page.get_by_role('button', name='保存')")
    assert ts == 'page.getByRole("button", { name: "保存" })'


@pytest.mark.unit
def test_py_loc_to_ts_plain_methods():
    assert py_loc_to_ts('page.get_by_test_id("x")') == 'page.getByTestId("x")'
    assert py_loc_to_ts("page.locator('#id')") == 'page.locator("#id")'
    assert (py_loc_to_ts("page.locator('[data-qa=\"s\"]')")
            == 'page.locator("[data-qa=\\"s\\"]")')
    assert py_loc_to_ts("page.get_by_placeholder('账')") \
        == 'page.getByPlaceholder("账")'
    assert py_loc_to_ts("page.get_by_text('hi')") == 'page.getByText("hi")'


@pytest.mark.unit
def test_py_loc_to_ts_passthrough():
    """非 page. 前缀 / 解析失败原样返回。"""
    assert py_loc_to_ts('') == ''
    assert py_loc_to_ts('css:#x') == 'css:#x'


# ─────────────────────────────────────────────────────────────────────────────
# py_expect_to_ts 断言转换
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_py_expect_to_ts_page_level():
    assert (py_expect_to_ts('expect(page).to_have_url("http://x")')
            == 'await expect(page).toHaveURL("http://x")')
    assert (py_expect_to_ts('expect(page).to_have_title("T")')
            == 'await expect(page).toHaveTitle("T")')


@pytest.mark.unit
def test_py_expect_to_ts_element_level():
    assert (py_expect_to_ts('expect(page.locator("#c")).not_to_be_checked()')
            == 'await expect(page.locator("#c")).not.toBeChecked()')
    assert (py_expect_to_ts('expect(page.get_by_role("button")).to_have_count(2)')
            == 'await expect(page.getByRole("button")).toHaveCount(2)')
    assert (py_expect_to_ts('expect(page.locator("#i")).to_have_value("v")')
            == 'await expect(page.locator("#i")).toHaveValue("v")')
    assert (py_expect_to_ts('expect(page.locator("#x")).to_have_accessible_name("y")')
            == 'await expect(page.locator("#x")).toHaveAccessibleName("y")')


# ─────────────────────────────────────────────────────────────────────────────
# record-export playwright-ts / playwright-ts-async
# ─────────────────────────────────────────────────────────────────────────────

_ACTIONS = [
    {'type': 'fill', 'locator': 'css:#u', 'value': 'bob',
     'element': {'tag': 'input', 'attrs': {'placeholder': '账号'},
                 'text': ''},
     'page': {'url': 'http://x/t.html'}},
    {'type': 'click', 'locator': 'css:#save',
     'element': {'tag': 'button', 'attrs': {'data-testid': 'save-btn'},
                 'text': '保存'},
     'page': {'url': 'http://x/t.html'}},
    {'type': '_final', 'page': {'url': 'http://x/done.html'}},
]


@pytest.mark.unit
def test_export_playwright_ts_structure():
    """playwright-ts：Playwright Test 风格（test fixture + import）。"""
    out = export_actions(_ACTIONS, 'playwright-ts')
    assert "import { test, expect } from '@playwright/test';" in out
    assert "test('dp-cli recording', async ({ page }) => {" in out
    assert 'await page.goto("http://x/t.html");' in out
    assert 'await page.getByPlaceholder("账号").fill("bob");' in out
    assert 'await page.getByTestId("save-btn").click();' in out
    # 导航同步点：_final 终态 url → toHaveURL
    assert 'await expect(page).toHaveURL("http://x/done.html");' in out
    assert out.rstrip().endswith('});')
    # 无 py 痕迹
    assert 'get_by_' not in out and 'to_have_' not in out


@pytest.mark.unit
def test_export_playwright_ts_async_structure():
    """playwright-ts-async：独立库脚本（chromium + async function）。"""
    out = export_actions(_ACTIONS, 'playwright-ts-async')
    assert "import { chromium, expect } from 'playwright';" in out
    assert 'async function run() {' in out
    assert 'await chromium.launch({ headless: false })' in out
    assert 'await browser.newPage()' in out
    assert 'await page.getByTestId("save-btn").click();' in out
    assert 'await expect(page).toHaveURL("http://x/done.html");' in out
    assert out.rstrip().endswith('run();')


@pytest.mark.unit
def test_export_ts_nav_sync_between_actions():
    """动作间 url 变化（非末次）也插 toHaveURL。"""
    actions = [
        {'type': 'click', 'locator': 'css:#a',
         'element': {'tag': 'a', 'attrs': {}, 'text': 'go'},
         'page': {'url': 'http://x/1'}},
        {'type': 'click', 'locator': 'css:#b',
         'element': {'tag': 'button', 'attrs': {}, 'text': 'ok'},
         'page': {'url': 'http://x/2'}},
    ]
    out = export_actions(actions, 'playwright-ts')
    assert 'await expect(page).toHaveURL("http://x/2");' in out


# ─────────────────────────────────────────────────────────────────────────────
# expect-gen --lang ts
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_expect_gen_lang_ts_page_level(runner, monkeypatch):
    page = FakePage(url='https://ex.com/dash', title='仪表盘')
    r = _invoke(runner, monkeypatch, page, ['expect-gen', '--lang', 'ts'])
    assert r.exit_code == 0
    d = _out(r)['data']
    assert d['lang'] == 'ts'
    assert 'await expect(page).toHaveURL("https://ex.com/dash")' in d['script']
    assert 'await expect(page).toHaveTitle("仪表盘")' in d['script']
    # ts 注释形态
    assert '// 当前 URL' in d['script']
    assert '# 当前 URL' not in d['script']


@pytest.mark.unit
def test_expect_gen_lang_ts_element(runner, monkeypatch):
    """--lang ts --ele：元素断言驼峰化 + 定位器驼峰化。"""
    info = {'tag': 'input', 'input_type': 'checkbox',
            'attrs': {'type': 'checkbox'}, 'text': '', 'visible': True,
            'accname': '同意', 'label': '', 'value': 'on',
            'checked': True, 'disabled': False}
    monkeypatch.setattr(assert_mod, '_ele_info', lambda ele: info)
    page = FakePage(ele=FakeEle(raw_text=''), run_js_return=1)
    r = _invoke(runner, monkeypatch, page,
                ['expect-gen', '--ele', '#ck', '--lang', 'ts'])
    assert r.exit_code == 0
    d = _out(r)['data']
    codes = [e['code'] for e in d['expects']]
    assert all(c.startswith('await expect(') for c in codes)
    assert any('toBeChecked()' in c for c in codes)
    assert any('toBeVisible()' in c for c in codes)
    assert any('toHaveCount(1)' in c for c in codes)
    assert any('getByRole("checkbox", { name: "同意" })' in c
               for c in codes)


@pytest.mark.unit
def test_expect_gen_default_lang_py_unchanged(runner, monkeypatch):
    """默认 --lang py 输出零变化（向后兼容不变式）。"""
    page = FakePage(url='https://ex.com', title='T')
    r = _invoke(runner, monkeypatch, page, ['expect-gen'])
    d = _out(r)['data']
    assert d['lang'] == 'py'
    assert 'await expect' not in d['script']
    assert 'to_have_url("https://ex.com")' in d['script']
    assert '# 当前 URL' in d['script']
