# -*- coding:utf-8 -*-
"""键盘与滚动命令: press / type / scroll / scroll-to / autoscroll"""
from time import sleep as _sleep

import click

from dp_cli.output import ok, error
from dp_cli.commands._utils import (
    session_option, _get_page, resolve_locator, wait_network_idle,
)


def register(cli):

    @cli.command('press')
    @click.argument('key')
    @session_option
    def cmd_press(key, session):
        """模拟键盘按键。

        \b
        支持的按键: Enter, Tab, Escape, Space, Backspace,
                   ArrowUp/Down/Left/Right, F1-F12,
                   Control+A, Shift+Enter, Alt+F4 等组合键。

        \b
        示例:
          dp press Enter
          dp press "Control+A"
          dp press Escape
        """
        page = _get_page(session)
        try:
            from DrissionPage._functions.keys import Keys
            key_map = {
                'enter': '\ue007', 'tab': '\ue004', 'escape': '\ue00c', 'esc': '\ue00c',
                'backspace': '\ue003', 'delete': '\ue017', 'space': ' ',
                'arrowup': '\ue013', 'arrowdown': '\ue015',
                'arrowleft': '\ue012', 'arrowright': '\ue014',
            }
            k = key.lower()
            if '+' in key:
                parts = key.split('+')
                modifier = parts[0].lower()
                main_key = parts[1]
                mod_map = {'control': Keys.CTRL, 'ctrl': Keys.CTRL,
                           'shift': Keys.SHIFT, 'alt': Keys.ALT}
                if modifier in mod_map:
                    page.actions.key_down(mod_map[modifier]).type(main_key).key_up(mod_map[modifier])
                else:
                    page.actions.type(key)
            else:
                actual_key = key_map.get(k, key)
                page.actions.type(actual_key)
            ok({'key': key}, msg='按键成功')
        except Exception as e:
            error(f'按键失败: {key}', code='KEY_FAILED', detail=str(e))

    @cli.command('type')
    @click.argument('text')
    @session_option
    def cmd_type(text, session):
        """输入文本（当前焦点元素）。

        \b
        示例:
          dp type "hello world"
          dp type "search query"
        """
        page = _get_page(session)
        try:
            page.actions.type(text)
            ok({'text': text}, msg='输入成功')
        except Exception as e:
            error(f'输入失败', code='TYPE_FAILED', detail=str(e))

    @cli.command('scroll')
    @click.option('--x', default=0, type=int, help='水平滚动像素')
    @click.option('--y', default=300, type=int, help='垂直滚动像素')
    @click.option('--top', is_flag=True, help='滚动到顶部')
    @click.option('--bottom', is_flag=True, help='滚动到底部')
    @click.option('--locator', default=None, help='滚动特定元素（而非页面）')
    @click.option('--mouse-x', default=None, type=int, help='locator 失效时按录制鼠标 X 坐标查找滚动容器')
    @click.option('--mouse-y', default=None, type=int, help='locator 失效时按录制鼠标 Y 坐标查找滚动容器')
    @session_option
    def cmd_scroll(x, y, top, bottom, locator, mouse_x, mouse_y, session):
        """滚动页面或元素。

        \b
        示例:
          dp scroll --y 300
          dp scroll --y -200
          dp scroll --top
          dp scroll --bottom
          dp scroll --locator "css:.scroll-container" --y 100
          dp scroll --locator "css:.scroll-container" --bottom
          dp scroll --y 100 --mouse-x 500 --mouse-y 600
        """
        locator = resolve_locator(locator, session) if locator else None
        page = _get_page(session)
        try:
            if locator:
                target = page.ele(locator)
                if not target or target.__class__.__name__ == 'NoneElement':
                    if mouse_x is None or mouse_y is None:
                        error(f'未找到滚动元素: {locator}', code='ELEMENT_NOT_FOUND')
                        return
                    locator = None
                    target = None
                if target:
                    if top:
                        result = target.run_js(
                            'this.scrollTop = 0; return {scrollTop: this.scrollTop, scrollLeft: this.scrollLeft};'
                        )
                        ok({'locator': locator, 'position': result}, msg='已滚动到顶部')
                    elif bottom:
                        result = target.run_js(
                            'this.scrollTop = this.scrollHeight; return {scrollTop: this.scrollTop, scrollLeft: this.scrollLeft};'
                        )
                        ok({'locator': locator, 'position': result}, msg='已滚动到底部')
                    else:
                        result = target.run_js(
                            """
                            const before = {scrollTop: this.scrollTop, scrollLeft: this.scrollLeft};
                            this.scrollTop += arguments[1];
                            this.scrollLeft += arguments[0];
                            this.dispatchEvent(new Event('scroll', {bubbles: true}));
                            return {
                              before,
                              after: {scrollTop: this.scrollTop, scrollLeft: this.scrollLeft},
                              scrollHeight: this.scrollHeight,
                              clientHeight: this.clientHeight,
                              scrollWidth: this.scrollWidth,
                              clientWidth: this.clientWidth,
                              mode: 'locator'
                            };
                            """,
                            x, y,
                        )
                        ok({'x': x, 'y': y, 'locator': locator, 'position': result}, msg='滚动成功')
                    return

            if mouse_x is not None and mouse_y is not None:
                result = page.run_js(
                    """
                    function findScrollable(el) {
                      while (el && el !== document.body && el !== document.documentElement) {
                        const st = getComputedStyle(el);
                        const canY = /(auto|scroll|overlay)/.test(st.overflowY) && el.scrollHeight > el.clientHeight + 1;
                        const canX = /(auto|scroll|overlay)/.test(st.overflowX) && el.scrollWidth > el.clientWidth + 1;
                        if (canY || canX) return el;
                        el = el.parentElement;
                      }
                      return document.scrollingElement || document.documentElement;
                    }
                    const start = document.elementFromPoint(arguments[2], arguments[3]);
                    const target = findScrollable(start);
                    const before = {scrollTop: target.scrollTop, scrollLeft: target.scrollLeft};
                    if (arguments[4]) {
                      target.scrollTop = 0;
                    } else if (arguments[5]) {
                      target.scrollTop = target.scrollHeight;
                    } else {
                      target.scrollTop += arguments[1];
                      target.scrollLeft += arguments[0];
                    }
                    target.dispatchEvent(new Event('scroll', {bubbles: true}));
                    return {
                      before,
                      after: {scrollTop: target.scrollTop, scrollLeft: target.scrollLeft},
                      scrollHeight: target.scrollHeight,
                      clientHeight: target.clientHeight,
                      scrollWidth: target.scrollWidth,
                      clientWidth: target.clientWidth,
                      tag: target.tagName,
                      id: target.id || '',
                      className: target.className || '',
                      mode: 'mouse'
                    };
                    """,
                    x, y, mouse_x, mouse_y, top, bottom,
                )
                ok({'x': x, 'y': y, 'mouse': {'x': mouse_x, 'y': mouse_y},
                    'position': result}, msg='滚动成功')
                return

            if top:
                result = page.run_js(
                    'window.scrollTo(0, 0); return {scrollX: window.scrollX, scrollY: window.scrollY};'
                )
                ok({'position': result}, msg='已滚动到顶部')
            elif bottom:
                result = page.run_js(
                    'window.scrollTo(0, document.scrollingElement.scrollHeight); return {scrollX: window.scrollX, scrollY: window.scrollY};'
                )
                ok({'position': result}, msg='已滚动到底部')
            else:
                result = page.run_js(
                    'window.scrollBy(arguments[0], arguments[1]); return {scrollX: window.scrollX, scrollY: window.scrollY};',
                    x, y,
                )
                ok({'x': x, 'y': y, 'position': result}, msg='滚动成功')
        except Exception as e:
            error(f'滚动失败', code='SCROLL_FAILED', detail=str(e))

    @cli.command('scroll-to')
    @click.argument('locator')
    @session_option
    def scroll_to(locator, session):
        """滚动页面直到元素可见。

        \b
        示例:
          dp scroll-to "#footer"
          dp scroll-to "ref:20"
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        try:
            ele = page.ele(locator)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                return
            ele.scroll.to_see()
            ok({'locator': locator}, msg='已滚动到元素')
        except Exception as e:
            error(f'滚动失败', code='SCROLL_FAILED', detail=str(e))

    @cli.command('autoscroll')
    @click.option('--locator', default=None,
                  help='计数元素（如 ".item"）；省略则用页面高度判断')
    @click.option('--container', default=None,
                  help='在指定容器内滚动（用于内部可滚动区域的 SPA）')
    @click.option('--max', 'max_rounds', default=300, show_default=True, type=int,
                  help='最大滚动轮数')
    @click.option('--stable', default=2, show_default=True, type=int,
                  help='连续 N 轮无增长视为到底')
    @click.option('--idle', default=2.0, show_default=True, type=float,
                  help='每轮滚动后等待网络空闲秒数（0 则仅固定小延时）')
    @click.option('--idle-timeout', default=10.0, show_default=True, type=float,
                  help='网络空闲等待超时（超时后视为本轮完成，不报错）')
    @click.option('--step', default=0, show_default=True, type=int,
                  help='每轮滚动像素；0 表示按可视高度自动计算')
    @click.option('--fast', is_flag=True, default=False,
                  help='快速滚动模式：使用更大步长和短延迟，适合无反爬场景')
    @click.option('--fast-delay', default=0.05, show_default=True, type=float,
                  help='快速模式每轮固定等待秒数')
    @click.option('--mouse-x', default=None, type=int,
                  help='container 失效时按鼠标 X 坐标查找滚动容器')
    @click.option('--mouse-y', default=None, type=int,
                  help='container 失效时按鼠标 Y 坐标查找滚动容器')
    @session_option
    def cmd_autoscroll(locator, container, max_rounds, stable, idle, idle_timeout,
                       step, fast, fast_delay, mouse_x, mouse_y, session):
        """循环滚动到底部，直到懒加载无新内容。

        \b
        终止条件:
          1. 连续 --stable 轮 计数/高度 无增长
          2. 达到 --max 轮上限

        \b
        示例:
          dp autoscroll --locator ".item"            # 按元素数量判断
          dp autoscroll                              # 按页面高度判断
          dp autoscroll --container "#feed" --idle 3 # 容器内滚动
          dp autoscroll --step 800 --max 50          # 每轮滚动 800px
          dp autoscroll --fast --max 100             # 速度优先，快速滚到底
          dp autoscroll --mouse-x 500 --mouse-y 600  # 按坐标查找滚动容器
          dp autoscroll --max 50 --stable 3          # 更耐心的配置
        """
        if locator:
            locator = resolve_locator(locator, session)
        if container:
            container = resolve_locator(container, session)
        page = _get_page(session)

        try:
            target = page.ele(container) if container else None
            if container and (not target or target.__class__.__name__ == 'NoneElement'):
                if mouse_x is None or mouse_y is None:
                    error(f'未找到容器: {container}', code='ELEMENT_NOT_FOUND')
                    return
                target = None

            use_mouse_container = target is None and mouse_x is not None and mouse_y is not None

            def _measure():
                if locator:
                    return len(page.eles(locator, timeout=0))
                if container and target:
                    return int(target.run_js('return this.scrollHeight'))
                if use_mouse_container:
                    return int(page.run_js(
                        """
                        function findScrollable(el) {
                          while (el && el !== document.body && el !== document.documentElement) {
                            const st = getComputedStyle(el);
                            const canY = /(auto|scroll|overlay)/.test(st.overflowY) && el.scrollHeight > el.clientHeight + 1;
                            if (canY) return el;
                            el = el.parentElement;
                          }
                          return document.scrollingElement || document.documentElement;
                        }
                        const target = findScrollable(document.elementFromPoint(arguments[0], arguments[1]));
                        return target.scrollHeight;
                        """,
                        mouse_x, mouse_y,
                    ))
                return int(page.run_js('return document.documentElement.scrollHeight'))

            metric = 'count' if locator else 'scrollHeight'
            history = [_measure()]
            positions = []
            stable_count = 0
            reason = 'max-reached'

            for i in range(max_rounds):
                if container and target:
                    position = target.run_js(
                        """
                        const before = this.scrollTop;
                        const delta = arguments[0] > 0 ? arguments[0] : Math.max(300, Math.floor(this.clientHeight * arguments[1]));
                        this.scrollTop += delta;
                        this.dispatchEvent(new Event('scroll', {bubbles: true}));
                        return {before, after: this.scrollTop, delta, scrollHeight: this.scrollHeight, clientHeight: this.clientHeight, mode: 'container'};
                        """,
                        step, 3 if fast else 0.9,
                    )
                elif use_mouse_container:
                    position = page.run_js(
                        """
                        function findScrollable(el) {
                          while (el && el !== document.body && el !== document.documentElement) {
                            const st = getComputedStyle(el);
                            const canY = /(auto|scroll|overlay)/.test(st.overflowY) && el.scrollHeight > el.clientHeight + 1;
                            if (canY) return el;
                            el = el.parentElement;
                          }
                          return document.scrollingElement || document.documentElement;
                        }
                        const target = findScrollable(document.elementFromPoint(arguments[0], arguments[1]));
                        const before = target.scrollTop;
                        const delta = arguments[2] > 0 ? arguments[2] : Math.max(300, Math.floor(target.clientHeight * arguments[3]));
                        target.scrollTop += delta;
                        target.dispatchEvent(new Event('scroll', {bubbles: true}));
                        return {
                          before,
                          after: target.scrollTop,
                          delta,
                          scrollHeight: target.scrollHeight,
                          clientHeight: target.clientHeight,
                          tag: target.tagName,
                          id: target.id || '',
                          className: target.className || '',
                          mode: 'mouse'
                        };
                        """,
                        mouse_x, mouse_y, step, 3 if fast else 0.9,
                    )
                else:
                    position = page.run_js(
                        """
                        const target = document.scrollingElement || document.documentElement;
                        const before = target.scrollTop;
                        const delta = arguments[0] > 0 ? arguments[0] : Math.max(300, Math.floor(window.innerHeight * arguments[1]));
                        window.scrollBy(0, delta);
                        return {before, after: window.scrollY || target.scrollTop, delta, scrollHeight: target.scrollHeight, clientHeight: target.clientHeight, mode: 'page'};
                        """,
                        step, 3 if fast else 0.9,
                    )
                positions.append(position)
                if fast:
                    _sleep(max(0, fast_delay))
                elif idle > 0:
                    try:
                        wait_network_idle(page, idle_time=idle, timeout=idle_timeout)
                    except TimeoutError:
                        pass  # 超时也继续判断，可能本来就没新请求
                else:
                    _sleep(0.3)

                curr = _measure()
                history.append(curr)
                moved = bool(position and position.get('after') != position.get('before'))
                if curr <= history[-2] and not moved:
                    stable_count += 1
                    if stable_count >= stable:
                        reason = 'stable'
                        break
                else:
                    stable_count = 0

            ok({
                'rounds': len(history) - 1,
                'metric': metric,
                'initial': history[0],
                'final': history[-1],
                'growth': history[-1] - history[0],
                'history': history,
                'positions': positions,
                'reason': reason,
            }, msg=f'autoscroll 完成 ({reason})：{metric} {history[0]} → {history[-1]}')
        except Exception as e:
            error(f'自动滚动失败', code='AUTOSCROLL_FAILED', detail=str(e))
