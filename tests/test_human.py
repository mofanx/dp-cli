# -*- coding:utf-8 -*-
"""
--human 拟人输入测试（t2）+ har-dump 版本号动态化。

FakeActions 记录链式调用序列，验证贝塞尔分段/随机间隔/键序。
"""
import json
from importlib.metadata import version as pkg_version

import pytest
from click.testing import CliRunner

import dp_cli.commands.element as emod
import dp_cli.commands.keyboard as kmod
import dp_cli.human as hmod
from dp_cli import human


class FakeActions:
    """记录 actions 链调用；curr_x/curr_y 模拟 DrissionPage 内部追踪。"""

    def __init__(self):
        self.calls = []
        self.curr_x = 100.0
        self.curr_y = 100.0

    def move(self, offset_x=0, offset_y=0, duration=.5):
        self.calls.append(('move', offset_x, offset_y, duration))
        self.curr_x += offset_x
        self.curr_y += offset_y
        return self

    def wait(self, second, scope=None):
        self.calls.append(('wait', second))
        return self

    def click(self, on_ele=None, times=1):
        self.calls.append(('click', times))
        return self

    def type(self, keys, interval=0):
        self.calls.append(('type', keys))
        return self

    def key_down(self, key):
        self.calls.append(('key_down', key))
        return self

    def key_up(self, key):
        self.calls.append(('key_up', key))
        return self


class _Rect:
    viewport_midpoint = (500, 300)
    midpoint = (500, 300)
    viewport_location = (490, 295)


class FakeEle:
    def __init__(self):
        self.rect = _Rect()
        self.clicked = []
        self.inputs = []

        class _Click:
            def __init__(self, outer):
                self._outer = outer

            def __call__(self, *a, **k):
                self._outer.clicked.append(('click', a, k))

            def js(self):
                self._outer.clicked.append(('js', (), {}))
        self.click = _Click(self)

    def input(self, value, clear=True, by_js=False):
        self.inputs.append((value, clear, by_js))


class _NoneEle:
    pass
_NoneEle.__name__ = 'NoneElement'


class FakePage:
    def __init__(self, ele=None):
        self._ele = ele
        self.actions = FakeActions()
        self.url = 'https://example.com'
        self.latest_tab = None

        class _States:
            ready_state = 'complete'
        self.states = _States()

    @property
    def title(self):
        return 'Demo'

    def ele(self, locator, index=1, timeout=10):
        return self._ele if self._ele is not None else _NoneEle()

    def eles(self, locator, timeout=10):
        return [self._ele] if self._ele is not None else []


@pytest.fixture
def runner():
    return CliRunner()


def _out(r):
    return json.loads(r.output)


@pytest.fixture
def fast_sleep(monkeypatch):
    """human_type 的字符间隔 sleep 加速（保持调用但零耗时）。"""
    sleeps = []
    monkeypatch.setattr(hmod, 'sleep', lambda s: sleeps.append(s))
    return sleeps


# ─────────────────────────────────────────────────────────────────────────────
# human.py 单元
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_bezier_waypoints_end_at_target():
    """贝塞尔最后一个 waypoint 精确落在目标点。"""
    pts = human._bezier_waypoints(0, 0, 300, 200)
    assert pts[-1] == pytest.approx((300, 200), abs=1e-6)
    assert len(pts) >= 4


@pytest.mark.unit
def test_bezier_waypoints_curved():
    """中点不落在直线上（有垂直扰动）——大概率路径非直线。"""
    curved = 0
    for _ in range(20):
        pts = human._bezier_waypoints(0, 0, 200, 0)
        mid = pts[len(pts) // 2]
        if abs(mid[1]) > 0.01:
            curved += 1
    assert curved > 0  # 扰动区间非零，应至少有一部分曲线偏离直线


@pytest.mark.unit
def test_human_move_to_reaches_target():
    page = FakePage()
    human.human_move_to(page, 400, 250)
    # 分段 move 后 curr 到达目标
    assert page.actions.curr_x == pytest.approx(400)
    assert page.actions.curr_y == pytest.approx(250)
    moves = [c for c in page.actions.calls if c[0] == 'move']
    assert len(moves) >= 4


@pytest.mark.unit
def test_human_click_sequence():
    page = FakePage()
    ele = FakeEle()
    human.human_click(page, ele, 0.01, 0.02)
    kinds = [c[0] for c in page.actions.calls]
    assert kinds.count('move') >= 4
    assert 'wait' in kinds and 'click' in kinds
    assert kinds.index('wait') > kinds.index('move')
    assert kinds[-1] == 'click'


# ─────────────────────────────────────────────────────────────────────────────
# click / dblclick --human
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_click_human_uses_actions(runner, monkeypatch, fast_sleep):
    """click --human：走 Actions 链（move→wait→click），不走 ele.click()。"""
    from dp_cli.main import cli
    ele = FakeEle()
    page = FakePage(ele=ele)
    monkeypatch.setattr(emod, '_get_page', lambda s: page)
    monkeypatch.setattr(emod, 'resolve_locator', lambda l, s: l)
    monkeypatch.setattr(emod, 'load_session', lambda s: {}) if hasattr(
        emod, 'load_session') else None
    # _utils.get_browser 兜底
    import dp_cli.commands._utils as u
    monkeypatch.setattr(u, 'load_session', lambda s: {})
    result = runner.invoke(
        cli, ['click', 'css:.btn', '--human', '--human-min', '0.001',
              '--human-max', '0.002'])
    assert result.exit_code == 0, result.output
    kinds = [c[0] for c in page.actions.calls]
    assert kinds.count('move') >= 4
    assert 'click' in kinds
    assert not ele.clicked  # 未走 ele.click()


@pytest.mark.unit
def test_click_human_conflicts_by_js(runner, monkeypatch):
    from dp_cli.main import cli
    import dp_cli.commands._utils as u
    monkeypatch.setattr(u, 'load_session', lambda s: {})
    result = runner.invoke(
        cli, ['click', 'css:.btn', '--human', '--by-js'])
    assert result.exit_code == 2
    assert _out(result)['code'] == 'INVALID_ARGS'


@pytest.mark.unit
def test_dblclick_human_times2(runner, monkeypatch):
    from dp_cli.main import cli
    import dp_cli.commands._utils as u
    ele = FakeEle()
    page = FakePage(ele=ele)
    monkeypatch.setattr(emod, '_get_page', lambda s: page)
    monkeypatch.setattr(emod, 'resolve_locator', lambda l, s: l)
    monkeypatch.setattr(u, 'load_session', lambda s: {})
    result = runner.invoke(
        cli, ['dblclick', 'css:.cell', '--human',
              '--human-min', '0.001', '--human-max', '0.002'])
    assert result.exit_code == 0, result.output
    clicks = [c for c in page.actions.calls if c[0] == 'click']
    assert clicks and clicks[-1][1] == 2


# ─────────────────────────────────────────────────────────────────────────────
# fill --human
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_fill_human_click_clear_type(runner, monkeypatch, fast_sleep):
    """fill --human：点击聚焦 → Ctrl+A+Backspace 清空 → 逐字符输入。"""
    from dp_cli.main import cli
    import dp_cli.commands._utils as u
    ele = FakeEle()
    page = FakePage(ele=ele)
    monkeypatch.setattr(emod, '_get_page', lambda s: page)
    monkeypatch.setattr(emod, 'resolve_locator', lambda l, s: l)
    monkeypatch.setattr(u, 'load_session', lambda s: {})
    result = runner.invoke(
        cli, ['fill', 'css:input', 'abc', '--human',
              '--human-min', '0.001', '--human-max', '0.002'])
    assert result.exit_code == 0, result.output
    calls = page.actions.calls
    kinds = [c[0] for c in calls]
    # 清空链：key_down + type('a') + key_up + type(backspace)
    assert 'key_down' in kinds and 'key_up' in kinds
    types = [c[1] for c in calls if c[0] == 'type']
    assert 'a' in types                      # Ctrl+A 的 'a'
    assert types[-3:] == ['a', 'b', 'c']     # 逐字符输入
    assert not ele.inputs                    # 未走 ele.input()


# ─────────────────────────────────────────────────────────────────────────────
# type / press --human
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_type_human_per_char(runner, monkeypatch, fast_sleep):
    from dp_cli.main import cli
    import dp_cli.commands._utils as u
    page = FakePage()
    monkeypatch.setattr(kmod, '_get_page', lambda s: page)
    monkeypatch.setattr(u, 'load_session', lambda s: {})
    result = runner.invoke(
        cli, ['type', 'hello', '--human',
              '--human-min', '0.001', '--human-max', '0.002'])
    assert result.exit_code == 0, result.output
    types = [c[1] for c in page.actions.calls if c[0] == 'type']
    assert types == list('hello')
    assert len(fast_sleep) == 5  # 每字符一次随机间隔


@pytest.mark.unit
def test_type_default_single_call(runner, monkeypatch):
    """不加 --human 保持原单次 type 行为。"""
    from dp_cli.main import cli
    import dp_cli.commands._utils as u
    page = FakePage()
    monkeypatch.setattr(kmod, '_get_page', lambda s: page)
    monkeypatch.setattr(u, 'load_session', lambda s: {})
    result = runner.invoke(cli, ['type', 'hello'])
    assert result.exit_code == 0
    types = [c[1] for c in page.actions.calls if c[0] == 'type']
    assert types == ['hello']


@pytest.mark.unit
def test_press_human_waits(runner, monkeypatch):
    """press --human 组合键：前置 wait + 键间 wait。"""
    from dp_cli.main import cli
    import dp_cli.commands._utils as u
    page = FakePage()
    monkeypatch.setattr(kmod, '_get_page', lambda s: page)
    monkeypatch.setattr(u, 'load_session', lambda s: {})
    result = runner.invoke(
        cli, ['press', 'Control+A', '--human',
              '--human-min', '0.001', '--human-max', '0.002'])
    assert result.exit_code == 0, result.output
    kinds = [c[0] for c in page.actions.calls]
    assert kinds[0] == 'wait'                       # 前置随机停顿
    assert 'key_down' in kinds and 'key_up' in kinds
    assert kinds.count('wait') >= 2


# ─────────────────────────────────────────────────────────────────────────────
# har-dump creator.version
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_pkg_version_dynamic():
    """HAR creator.version 动态读包版本，不再硬编码。"""
    import dp_cli.commands.network as nmod
    assert nmod._pkg_version() == pkg_version('dp-cli')
    # 兜底路径：metadata 缺失时返回 'unknown'
    import importlib.metadata as m
    orig = m.version
    try:
        m.version = lambda name: (_ for _ in ()).throw(
            m.PackageNotFoundError(name))
        assert nmod._pkg_version() == 'unknown'
    finally:
        m.version = orig


@pytest.mark.unit
def test_human_min_max_semantics_documented():
    """--human-min/max 语义明确：停顿（点击前停留/逐字符间隔）随机区间，
    帮助字符串必须点明适用对象，避免歧义。"""
    import inspect
    src = inspect.getsource(human.human_options)
    assert '点击前停留' in src and '逐字符' in src
    assert '随机' in src
    # 模块 docstring 同步澄清不含移动分段时长
    assert '停顿' in human.__doc__ and '移动分段' in human.__doc__
