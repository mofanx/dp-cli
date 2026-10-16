# -*- coding:utf-8 -*-
"""wait/cf-wait 事件驱动（CdpWaitGate）回归测试"""
import json
import threading
import time

import pytest
from click.testing import CliRunner

from dp_cli.main import cli
from dp_cli.commands._utils import CdpWaitGate
import dp_cli.commands.browser as bmod
import dp_cli.commands.page as pmod


runner = CliRunner()


class _FakeDriver:
    """记录 set_callback 并可手动触发事件的假 driver。"""
    def __init__(self):
        self.callbacks = {}

    def set_callback(self, event, cb):
        if cb is None:
            self.callbacks.pop(event, None)
        else:
            self.callbacks[event] = cb

    def fire(self, event, **kw):
        cb = self.callbacks.get(event)
        if cb:
            cb(**kw)


class _GatePage:
    """具备 driver/run_cdp/run_js 的假页面（arm 可成功）。"""
    def __init__(self, js_seq=None, fail_cdp=False):
        self.driver = _FakeDriver()
        self.cdp_calls = []
        self.js_calls = []
        self._fail_cdp = fail_cdp
        self._seq = iter(js_seq) if js_seq else None

    def run_cdp(self, method, **kw):
        if self._fail_cdp:
            raise RuntimeError('cdp down')
        self.cdp_calls.append(method)
        return {}

    def run_js(self, script, *a, **k):
        self.js_calls.append(script)
        if '__dp_wait_armed' in script:        # watcher 注入
            return 'ok'
        if self._seq is not None:
            v = next(self._seq)
            if isinstance(v, Exception):
                raise v
            return v
        return None

    @property
    def html(self):
        return ''


# ── CdpWaitGate 单元级 ──────────────────────────────────────────────────

@pytest.mark.unit
def test_gate_arm_registers_binding_and_events():
    """arm：Runtime.enable + addBinding + Page.enable + 4 回调注册 + watcher 注入。"""
    page = _GatePage()
    gate = CdpWaitGate(page)
    assert gate.arm('window.ready===true') is True
    assert {'Runtime.enable', 'Runtime.addBinding', 'Page.enable'} \
        <= set(page.cdp_calls)
    assert set(page.driver.callbacks) == set(CdpWaitGate._EVENTS)
    assert any('window.ready===true' in s for s in page.js_calls)
    gate.disarm()
    assert not page.driver.callbacks      # 回调全部摘除


@pytest.mark.unit
def test_gate_arm_failure_falls_back():
    """run_cdp 失败 → armed=False，调用方可走纯轮询。"""
    page = _GatePage(fail_cdp=True)
    gate = CdpWaitGate(page)
    assert gate.arm('x') is False
    assert gate.armed is False


@pytest.mark.unit
def test_gate_force_poll_skips_cdp():
    """force_poll：不触碰浏览器，直接回落。"""
    page = _GatePage()
    gate = CdpWaitGate(page)
    assert gate.arm('x', force_poll=True) is False
    assert not page.cdp_calls and not page.js_calls


@pytest.mark.unit
def test_gate_wait_wakes_on_binding_event():
    """bindingCalled 事件唤醒 wait（不等 poll 超时）。"""
    page = _GatePage()
    gate = CdpWaitGate(page)
    gate.arm('x')
    threading.Timer(0.05, lambda: page.driver.fire(
        'Runtime.bindingCalled', name='__dp_wait', payload='hit')).start()
    t0 = time.perf_counter()
    assert gate.wait(5.0) is True
    assert time.perf_counter() - t0 < 1.0
    gate.disarm()


@pytest.mark.unit
def test_gate_nav_event_triggers_reinject():
    """Page.frameNavigated → 唤醒 + watcher 重注入。"""
    page = _GatePage()
    gate = CdpWaitGate(page)
    gate.arm('window.ready===true')
    n_inject = len(page.js_calls)
    threading.Timer(0.05, lambda: page.driver.fire(
        'Page.frameNavigated', frame={})).start()
    gate.wait(5.0)
    assert len(page.js_calls) == n_inject + 1   # 重注入一次
    gate.disarm()


@pytest.mark.unit
def test_gate_wait_poll_fallback_timeout():
    """无事件时 wait(seconds) 按节拍超时返回 False。"""
    page = _GatePage()
    gate = CdpWaitGate(page)
    gate.arm('x')
    assert gate.wait(0.02) is False
    gate.disarm()


@pytest.mark.unit
def test_watcher_self_cleanup_on_done():
    """完成态后 watcher 必须自停：命中即 clearInterval + observer.disconnect，
    不再空转（修复 done 后 interval/observer 持续占资源）。"""
    w = CdpWaitGate._WATCHER
    # 命中路径先 fin() 自清理再上报
    assert 'clearInterval' in w and 'disconnect' in w
    assert 'window.__dp_wait_ob' in w          # observer 持有引用以便断开
    assert 'window.__dp_wait_iv' in w          # interval 持有引用以便清除
    assert 'done=true' in w                    # done 标记仍在（防重入）


@pytest.mark.unit
def test_gate_disarm_injects_page_cleanup():
    """disarm：除摘除 CDP 回调外，还向页面注入清理脚本——
    超时/放弃场景下 watcher 不残留空转。"""
    page = _GatePage()
    gate = CdpWaitGate(page)
    gate.arm('window.ready===true')
    page.js_calls.clear()
    gate.disarm()
    assert any('clearInterval' in s and 'disconnect' in s
               for s in page.js_calls)
    assert not page.driver.callbacks


@pytest.mark.unit
def test_gate_disarm_cleanup_failure_tolerated():
    """页面已死时 disarm 清理注入失败不抛（watcher 随文档销毁）。"""
    page = _GatePage()
    gate = CdpWaitGate(page)
    gate.arm('window.ready===true')
    page.run_js = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError('page dead'))
    gate.disarm()           # 不应抛
    assert gate.armed is False


# ── wait --js 集成 ─────────────────────────────────────────────────────

@pytest.mark.unit
def test_wait_js_event_driven(monkeypatch):
    """--js：armed 路径——binding 事件唤醒后立即命中。"""
    page = _GatePage(js_seq=[None] * 50)
    monkeypatch.setattr(pmod, '_get_page', lambda s: page)
    # 第 2 次检查后点燃 binding → 第 3 次返回真值
    calls = {'n': 0}
    orig_run_js = page.run_js

    def run_js(script, *a, **k):
        if '__dp_wait_armed' not in script:
            calls['n'] += 1
            if calls['n'] == 2:
                page.driver.fire('Runtime.bindingCalled',
                                 name='__dp_wait', payload='hit')
        if calls['n'] >= 2 and '__dp_wait_armed' not in script:
            return 1
        return orig_run_js(script, *a, **k)

    page.run_js = run_js
    t0 = time.perf_counter()
    r = runner.invoke(cli, ['wait', '--js', 'window.ok', '--timeout', '5'])
    assert r.exit_code == 0, r.output
    assert time.perf_counter() - t0 < 2.0      # 事件唤醒，非轮询爬满
    assert 'Runtime.addBinding' in page.cdp_calls


@pytest.mark.unit
def test_wait_js_force_poll_no_cdp(monkeypatch):
    """--poll 强制轮询：不注册 binding/回调。"""
    page = _GatePage(js_seq=[None, None, 7])
    monkeypatch.setattr(pmod, '_get_page', lambda s: page)
    r = runner.invoke(cli, ['wait', '--js', 'window.ok', '--poll',
                            '--timeout', '5'])
    assert r.exit_code == 0, r.output
    assert not page.cdp_calls and not page.driver.callbacks


@pytest.mark.unit
def test_wait_js_poll_fallback_when_arm_fails(monkeypatch):
    """arm 失败（无 driver）→ 纯轮询回落，行为不变。"""
    class Bare:
        def run_js(self, script, *a, **k):
            return 'yes'                       # 立即真值
    monkeypatch.setattr(pmod, '_get_page', lambda s: Bare())
    r = runner.invoke(cli, ['wait', '--js', 'window.ok', '--timeout', '5'])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)['data']['result'] == 'yes'


# ── cf-wait 集成 ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_cf_wait_event_driven_still_conservative(monkeypatch):
    """armed 路径保留瞬断保守语义：瞬断→继续等→事件唤醒→检测通过。"""
    seq = iter([RuntimeError('context destroyed'),      # 瞬断
                {'f': True, 't': 'Just a moment...'},   # 仍在挑战
                {'f': False, 't': 'My Site'}])
    page = _GatePage()
    orig = page.run_js

    def run_js(script, *a, **k):
        if '__dp_wait_armed' in script:
            return 'ok'
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return v

    page.run_js = run_js
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    r = runner.invoke(cli, ['cf-wait', '--timeout', '10', '--poll', '0.01'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert d['passed'] is True and d['was_challenged'] is True
    assert d['title'] == 'My Site'
    assert 'Runtime.addBinding' in page.cdp_calls


@pytest.mark.unit
def test_cf_wait_no_events_flag(monkeypatch):
    """--no-events：不注册事件，纯轮询路径，超时语义不变。"""
    page = _GatePage(js_seq=[{'f': True, 't': 'Just a moment...'}] * 999)
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    r = runner.invoke(cli, ['cf-wait', '--timeout', '0.05', '--poll', '0.01',
                            '--no-events'])
    assert r.exit_code == 5
    assert json.loads(r.output)['code'] == 'WAIT_TIMEOUT'
    assert not page.cdp_calls


@pytest.mark.unit
def test_cf_wait_detect_js_is_expression(monkeypatch):
    """回归：detect_js 必须是合法表达式（Runtime.evaluate 顶层 return 非法）。

    既存 bug：'return {...}' + as_expr=True → SyntaxError → 保守分支
    永远判"挑战中"→ 必然超时。此用例守护脚本形态。
    """
    seen = []

    class P:
        def run_js(self, script, *a, **k):
            seen.append(script)
            return {'f': False, 't': 'ok'}

    monkeypatch.setattr(bmod, '_get_page', lambda s: P())
    r = runner.invoke(cli, ['cf-wait', '--timeout', '5', '--poll', '0.01',
                            '--no-events'])
    assert r.exit_code == 0, r.output
    detect_calls = [s for s in seen if 'challenges.cloudflare.com' in s
                    and '__dp_wait_armed' not in s]
    assert detect_calls
    assert not detect_calls[0].strip().startswith('return')


@pytest.mark.unit
def test_cf_wait_timeout_unchanged_with_events(monkeypatch):
    """armed 路径超时：WAIT_TIMEOUT exit5 契约不变。"""
    page = _GatePage(js_seq=[{'f': True, 't': 'Just a moment...'}] * 999)
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    r = runner.invoke(cli, ['cf-wait', '--timeout', '0.05', '--poll', '0.01'])
    assert r.exit_code == 5
    assert json.loads(r.output)['code'] == 'WAIT_TIMEOUT'
