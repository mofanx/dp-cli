# -*- coding:utf-8 -*-
"""拟人输入（--human）：贝塞尔轨迹移动 + 随机化间隔。

底层走 DrissionPage Actions 链（CDP Input.dispatchMouseEvent/dispatchKeyEvent），
在原生 move()/type() 之上叠加：
  - 移动轨迹按三次贝塞尔曲线分段，带垂直方向随机扰动（非直线匀速）
  - 每段/每字符间隔在 [human_min, human_max] 区间内随机
"""
import random
from math import hypot
from time import sleep

import click

DEFAULT_MIN = 0.05
DEFAULT_MAX = 0.18


def human_options(f):
    """click 选项组：--human / --human-min / --human-max。"""
    f = click.option(
        '--human-max', 'human_max', default=DEFAULT_MAX, type=float,
        show_default=True, help='--human 随机间隔上限（秒）')(f)
    f = click.option(
        '--human-min', 'human_min', default=DEFAULT_MIN, type=float,
        show_default=True, help='--human 随机间隔下限（秒）')(f)
    f = click.option(
        '--human', is_flag=True,
        help='拟人模式：贝塞尔轨迹移动 + 随机间隔输入（弱化自动化指纹）')(f)
    return f


def _rand(mn: float, mx: float) -> float:
    return random.uniform(mn, mx)


def _bezier_waypoints(x0, y0, x1, y1, n=None):
    """生成三次贝塞尔曲线的途径点（含垂直方向随机扰动）。

    n 缺省时按距离自适应（4-14 段）。
    """
    dist = hypot(x1 - x0, y1 - y0)
    if n is None:
        n = max(4, min(14, int(dist / 40) + 4))
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    nx, ny = -(y1 - y0), (x1 - x0)
    nl = hypot(nx, ny) or 1.0
    span = min(dist, 320)
    j1 = random.uniform(-0.35, 0.35) * span
    j2 = random.uniform(-0.2, 0.2) * span
    cx1 = mx + nx / nl * j1
    cy1 = my + ny / nl * j1
    cx2 = mx + nx / nl * j2
    cy2 = my + ny / nl * j2
    pts = []
    for i in range(1, n + 1):
        t = i / n
        mt = 1 - t
        pts.append((
            mt * mt * mt * x0 + 3 * mt * mt * t * cx1
            + 3 * mt * t * t * cx2 + t * t * t * x1,
            mt * mt * mt * y0 + 3 * mt * mt * t * cy1
            + 3 * mt * t * t * cy2 + t * t * t * y1,
        ))
    return pts


def human_move_to(page, x, y):
    """经贝塞尔 waypoint 分段调用 Actions.move 移到目标点，返回 actions 链。"""
    act = page.actions
    cx = getattr(act, 'curr_x', 0)
    cy = getattr(act, 'curr_y', 0)
    for px, py in _bezier_waypoints(cx, cy, x, y):
        act = act.move(px - cx, py - cy, duration=random.uniform(0.01, 0.05))
        cx, cy = px, py
    return act


def _ele_point(ele):
    """取元素视口中点（viewport_midpoint 优先，退化为 midpoint/location）。"""
    rect = getattr(ele, 'rect', None)
    for name in ('viewport_midpoint', 'midpoint', 'viewport_location'):
        pt = getattr(rect, name, None) if rect is not None else None
        if pt is not None:
            return pt
    return None


def human_click(page, ele, human_min=DEFAULT_MIN, human_max=DEFAULT_MAX,
                times=1):
    """拟人点击：贝塞尔移动到元素中点 → 随机停留 → 点击。"""
    pt = _ele_point(ele)
    act = page.actions
    if pt is not None:
        act = human_move_to(page, pt[0], pt[1])
    act.wait(_rand(human_min, human_max)).click(times=times)
    return act


def human_type(page, text, human_min=DEFAULT_MIN, human_max=DEFAULT_MAX):
    """逐字符输入，字符间随机停顿。"""
    act = page.actions
    for ch in text:
        act.type(ch)
        sleep(_rand(human_min, human_max))
    return act


def human_clear(page):
    """拟人清空输入框：Ctrl+A + Backspace（actions 链）。"""
    from DrissionPage._functions.keys import Keys
    act = page.actions
    return (act.key_down(Keys.CTRL)
            .type('a')
            .key_up(Keys.CTRL)
            .type('\ue003'))
