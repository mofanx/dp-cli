# -*- coding:utf-8 -*-
"""键盘与滚动命令: press / type / scroll / scroll-to / autoscroll"""
import time
from time import sleep as _sleep

import click

from dp_cli.output import ok, error, debug
from dp_cli.commands._utils import (
    session_option, _get_page, resolve_locator, wait_network_idle,
)
from dp_cli.hints import attach_hints, touched_apis
from dp_cli.session import load_session, save_session
from dp_cli.human import human_options, human_type, _rand

# ponytail: 提取重复的 findScrollable JS 函数
_FIND_SCROLLABLE_JS = """
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
"""


def _key_maps():
    from DrissionPage._functions.keys import Keys
    key_map = {
        'enter': '\ue007', 'tab': '\ue004', 'escape': '\ue00c', 'esc': '\ue00c',
        'backspace': '\ue003', 'delete': '\ue017', 'space': ' ',
        'arrowup': '\ue013', 'arrowdown': '\ue015',
        'arrowleft': '\ue012', 'arrowright': '\ue014',
    }
    mod_map = {'control': Keys.CTRL, 'ctrl': Keys.CTRL,
               'shift': Keys.SHIFT, 'alt': Keys.ALT,
               'meta': Keys.META, 'cmd': Keys.COMMAND,
               'command': Keys.COMMAND}
    # 常见键名别名 → Keys 枚举名（枚举用下划线风格，如 PAGE_UP；
    # 映射失败会回退为字面输入，带修饰键时会误触浏览器快捷键，
    # 所以别名必须覆盖常用名）
    key_alias = {
        'pageup': 'PAGE_UP', 'pgup': 'PAGE_UP',
        'pagedown': 'PAGE_DOWN', 'pgdn': 'PAGE_DOWN',
        'del': 'DEL', 'esc': 'ESCAPE', 'win': 'META',
    }

    def _key_token(name):
        """键名 → Keys 枚举/特殊键码；未知键按字面输入。"""
        low = name.strip().lower()
        if low in key_map:
            return key_map[low]
        attr = key_alias.get(
            low, low.upper().replace('-', '_').replace(' ', '_'))
        k = getattr(Keys, attr, None)
        return k if k is not None else name.strip()

    return mod_map, _key_token


def register(cli):

    @cli.command('press')
    @click.argument('key')
    @session_option
    @human_options
    def cmd_press(key, session, human, human_min, human_max):
        """模拟键盘按键。

        \b
        支持的按键: Enter, Tab, Escape, Space, Backspace,
                   ArrowUp/Down/Left/Right, F1-F12, Home, End,
                   PageUp/PageDown, Insert, Delete 等。
        组合键: Control+A, Shift+Enter, Alt+F4,
               支持多修饰键如 Control+Shift+Delete。

        \b
        示例:
          dpx press Enter
          dpx press "Control+A"
          dpx press "Control+Shift+Delete"
          dpx press F5
          dpx press Escape
        """
        page = _get_page(session)
        try:
            mod_map, _key_token = _key_maps()
            if human:
                page.actions.wait(_rand(human_min, human_max))
            if '+' in key:
                # 前缀修饰键逐个出栈，支持 Control+Shift+Delete 多修饰组合
                parts = [p for p in key.split('+') if p.strip()]
                mods = []
                while len(parts) > 1 and parts[0].strip().lower() in mod_map:
                    mods.append(mod_map[parts.pop(0).strip().lower()])
                if mods and parts:
                    act = page.actions
                    for m in mods:
                        act = act.key_down(m)
                        if human:
                            act = act.wait(_rand(human_min, human_max))
                    act = act.type(_key_token('+'.join(parts)))
                    if human:
                        act = act.wait(_rand(human_min, human_max))
                    for m in reversed(mods):
                        act = act.key_up(m)
                else:
                    page.actions.type(key)
            else:
                page.actions.type(_key_token(key))
            ok({'key': key}, msg='按键成功')
        except Exception as e:
            error(f'按键失败: {key}', code='KEY_FAILED', detail=str(e))

    @cli.command('type')
    @click.argument('text')
    @session_option
    @human_options
    def cmd_type(text, session, human, human_min, human_max):
        """输入文本（当前焦点元素）。

        成功响应 data.hints.touched_apis：动作 ±窗口内 netlog 捕获的
        XHR（serve/daemon 托管时；advisory，DPX_API_HINT=0 关）。

        \b
        示例:
          dpx type "hello world"
          dpx type "search query"
          dpx type "hello" --human   # 逐字符随机间隔输入
        """
        page = _get_page(session)
        try:
            _t0 = time.time()      # touched_apis 因果窗口锚（动作时刻）
            if human:
                human_type(page, text, human_min, human_max)
            else:
                page.actions.type(text)
            ok(attach_hints(
                {'text': text},
                touched_apis=touched_apis(session, _t0)),
               msg='输入成功')
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
          dpx scroll --y 300
          dpx scroll --y -200
          dpx scroll --top
          dpx scroll --bottom
          dpx scroll --locator "css:.scroll-container" --y 100
          dpx scroll --locator "css:.scroll-container" --bottom
          dpx scroll --y 100 --mouse-x 500 --mouse-y 600
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
                    f"""{_FIND_SCROLLABLE_JS}
                    const start = document.elementFromPoint(arguments[2], arguments[3]);
                    const target = findScrollable(start);
                    const before = {{scrollTop: target.scrollTop, scrollLeft: target.scrollLeft}};
                    if (arguments[4]) {{
                      target.scrollTop = 0;
                    }} else if (arguments[5]) {{
                      target.scrollTop = target.scrollHeight;
                    }} else {{
                      target.scrollTop += arguments[1];
                      target.scrollLeft += arguments[0];
                    }}
                    target.dispatchEvent(new Event('scroll', {{bubbles: true}}));
                    return {{
                      before,
                      after: {{scrollTop: target.scrollTop, scrollLeft: target.scrollLeft}},
                      scrollHeight: target.scrollHeight,
                      clientHeight: target.clientHeight,
                      scrollWidth: target.scrollWidth,
                      clientWidth: target.clientWidth,
                      tag: target.tagName,
                      id: target.id || '',
                      className: target.className || '',
                      mode: 'mouse'
                    }};
                    }}""",
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
          dpx scroll-to "#footer"
          dpx scroll-to "ref:20"
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
          dpx autoscroll --locator ".item"            # 按元素数量判断
          dpx autoscroll                              # 按页面高度判断
          dpx autoscroll --container "#feed" --idle 3 # 容器内滚动
          dpx autoscroll --step 800 --max 50          # 每轮滚动 800px
          dpx autoscroll --fast --max 100             # 速度优先，快速滚到底
          dpx autoscroll --mouse-x 500 --mouse-y 600  # 按坐标查找滚动容器
          dpx autoscroll --max 50 --stable 3          # 更耐心的配置
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
                        f"""{_FIND_SCROLLABLE_JS}
                        const target = findScrollable(document.elementFromPoint(arguments[0], arguments[1]));
                        return target.scrollHeight;""",
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
                        f"""{_FIND_SCROLLABLE_JS}
                        const target = findScrollable(document.elementFromPoint(arguments[0], arguments[1]));
                        const before = target.scrollTop;
                        const delta = arguments[2] > 0 ? arguments[2] : Math.max(300, Math.floor(target.clientHeight * arguments[3]));
                        target.scrollTop += delta;
                        target.dispatchEvent(new Event('scroll', {{bubbles: true}}));
                        return {{
                          before,
                          after: target.scrollTop,
                          delta,
                          scrollHeight: target.scrollHeight,
                          clientHeight: target.clientHeight,
                          tag: target.tagName,
                          id: target.id || '',
                          className: target.className || '',
                          mode: 'mouse'
                        }};""",
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
                        # 超时也继续判断，可能本来就没新请求
                        debug('wait_network_idle 超时，继续轮询')
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

    # ── 细分输入事件（对齐 playwright-cli keydown/keyup/mouse* 家族）──

    @cli.command('keydown')
    @click.argument('key')
    @session_option
    def cmd_keydown(key, session):
        """按下键不放（配合 keyup 实现长按/组合态）。

        \b
        示例: dpx keydown Shift → dpx click e3 → dpx keyup Shift
        """
        page = _get_page(session)
        try:
            _, token = _key_maps()
            page.actions.key_down(token(key))
            ok({'key': key}, msg='keydown')
        except Exception as e:
            error(f'keydown 失败: {key}', code='KEY_FAILED', detail=str(e))

    @cli.command('keyup')
    @click.argument('key')
    @session_option
    def cmd_keyup(key, session):
        """释放按住的键。"""
        page = _get_page(session)
        try:
            _, token = _key_maps()
            page.actions.key_up(token(key))
            ok({'key': key}, msg='keyup')
        except Exception as e:
            error(f'keyup 失败: {key}', code='KEY_FAILED', detail=str(e))

    def _mouse_pos(session, sess=None):
        """读 session 里记录的光标位置（跨进程命令间持久），默认 (0,0)。"""
        sess = sess if sess is not None else (load_session(session) or {})
        p = sess.get('mouse_pos') or [0, 0]
        return int(p[0]), int(p[1])

    def _save_mouse_pos(session, sess, x, y):
        sess['mouse_pos'] = [x, y]
        save_session(session, sess)

    _BUTTONS = {'left': 1, 'right': 2, 'middle': 4}

    @cli.command('mousemove')
    @click.argument('x', type=int)
    @click.argument('y', type=int)
    @session_option
    def cmd_mousemove(x, y, session):
        """移动鼠标到页面坐标 (x, y)。"""
        page = _get_page(session)
        try:
            page.run_cdp('Input.dispatchMouseEvent', type='mouseMoved',
                         x=x, y=y, buttons=0)
            _save_mouse_pos(session, load_session(session) or {}, x, y)
            ok({'x': x, 'y': y}, msg='鼠标已移动')
        except Exception as e:
            error('mousemove 失败', code='MOUSE_FAILED', detail=str(e))

    @cli.command('mousedown')
    @click.argument('button', default='left', required=False,
                    type=click.Choice(['left', 'right', 'middle']))
    @session_option
    def cmd_mousedown(button, session):
        """在当前光标位置按下鼠标键（配合 mouseup 自定义拖拽）。"""
        page = _get_page(session)
        sess = load_session(session) or {}
        x, y = _mouse_pos(session, sess)
        try:
            page.run_cdp('Input.dispatchMouseEvent', type='mousePressed',
                         x=x, y=y, button=button, clickCount=1,
                         buttons=_BUTTONS[button])
            ok({'button': button, 'x': x, 'y': y}, msg='鼠标已按下')
        except Exception as e:
            error('mousedown 失败', code='MOUSE_FAILED', detail=str(e))

    @cli.command('mouseup')
    @click.argument('button', default='left', required=False,
                    type=click.Choice(['left', 'right', 'middle']))
    @session_option
    def cmd_mouseup(button, session):
        """在当前光标位置释放鼠标键。"""
        page = _get_page(session)
        sess = load_session(session) or {}
        x, y = _mouse_pos(session, sess)
        try:
            page.run_cdp('Input.dispatchMouseEvent', type='mouseReleased',
                         x=x, y=y, button=button, clickCount=1,
                         buttons=0)
            ok({'button': button, 'x': x, 'y': y}, msg='鼠标已释放')
        except Exception as e:
            error('mouseup 失败', code='MOUSE_FAILED', detail=str(e))

    @cli.command('mousewheel')
    @click.argument('dx', type=int)
    @click.argument('dy', type=int)
    @session_option
    def cmd_mousewheel(dx, dy, session):
        """按像素滚动鼠标滚轮（dy>0 向下）。"""
        page = _get_page(session)
        sess = load_session(session) or {}
        x, y = _mouse_pos(session, sess)
        try:
            page.run_cdp('Input.dispatchMouseEvent', type='mouseWheel',
                         x=x, y=y, deltaX=dx, deltaY=dy, buttons=0)
            ok({'dx': dx, 'dy': dy}, msg='滚轮已滚动')
        except Exception as e:
            error('mousewheel 失败', code='MOUSE_FAILED', detail=str(e))

    @cli.command('devtools')
    @session_option
    def cmd_devtools(session):
        """打开 DevTools 面板（发送 F12）。仅有头浏览器有效。"""
        page = _get_page(session)
        try:
            _, token = _key_maps()
            page.actions.type(token('f12'))
            ok({}, msg='DevTools 已触发（F12）')
        except Exception as e:
            error('打开 DevTools 失败', code='MOUSE_FAILED', detail=str(e))
