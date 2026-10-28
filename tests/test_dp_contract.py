# -*- coding:utf-8 -*-
"""DrissionPage API 存在性契约测试——断言本项目调用点用到的方法
真实存在。历史踩坑：click(count=2)/clicker.js/tab_ids 都是
"以为有但没有"的 API，mock 测试永远抓不到，只能契约断言。
"""
import pytest

pytestmark = pytest.mark.unit


def _dp():
    try:
        from DrissionPage import ChromiumPage, ChromiumTab  # noqa
        from DrissionPage._elements.chromium_element import (
            ChromiumElement)
        return ChromiumElement
    except ImportError:
        pytest.skip('DrissionPage 未安装')


def test_click_methods_exist():
    """ele.click 系列：multi()/at() 存在（dblclick/by-mouse 依赖）。"""
    E = _dp()
    from DrissionPage._units.clicker import Clicker
    for m in ('multi', 'at', 'left', 'right', '__call__'):
        assert hasattr(Clicker, m), f'Clicker 缺 {m}'


def test_element_methods_exist():
    """ChromiumElement 关键方法（本项目所有调用点）。"""
    E = _dp()
    for m in ('click', 'run_js', 'input', 'select', 'hover', 'check',
              'drag_to', 'ele', 'eles', 'parent', 'states', 'rect',
              'attr', 'text', 'set', 'get'):
        assert hasattr(E, m), f'ChromiumElement 缺 {m}'


def test_page_methods_exist():
    """ChromiumPage/MixTab 本项目用到的页面级 API。"""
    from DrissionPage import ChromiumPage
    for m in ('get', 'run_js', 'run_js_loaded', 'ele', 'eles',
              'listen', 'get_frame', 'get_screenshot', 'run_cdp', 'url',
              'title', 'cookies', 'download', 'wait', 'actions',
              'latest_tab', 'tab_ids', 'browser', 'set', 'tab_id'):
        assert hasattr(ChromiumPage, m), f'ChromiumPage 缺 {m}'


def test_driver_callback_api_exist():
    """driver.set_callback + event_handlers 取值口——netlog 挂载与
    har-dump 快照/恢复路径共同依赖（set_callback 是单槽覆盖语义）。"""
    try:
        from DrissionPage._base.driver import Driver
    except ImportError:
        pytest.skip('DrissionPage 未安装')
    assert hasattr(Driver, 'set_callback')
    import inspect
    # event_handlers 是 set_callback 的实际存储/取值口（无独立 getter）
    assert 'event_handlers' in inspect.getsource(Driver.set_callback)


def test_browser_methods_exist():
    """Chromium 浏览器级 API（popup/tab 枚举依赖）。"""
    try:
        from DrissionPage import Chromium
    except ImportError:
        pytest.skip('Chromium 不存在')
    for m in ('tab_ids', 'latest_tab', 'get_tab', 'new_tab',
              'quit', 'states'):
        assert hasattr(Chromium, m), f'Chromium 缺 {m}'


def test_no_regressed_api_calls():
    """历史踩坑 API 回归：本项目源码不得引用已证不存在的调用。"""
    import inspect
    import dp_cli.commands.element as el
    src = inspect.getsource(el)
    # dblclick 曾用 click(count=2)——Clicker.__call__ 不支持 count
    assert 'click(count=' not in src
    # clicker.js 不存在（--by-js 踩坑）——应走 run_js
    assert 'clicker.js' not in src and '.js(' not in src
