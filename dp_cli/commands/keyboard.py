# -*- coding:utf-8 -*-
"""键盘与滚动命令: press / type / scroll / scroll-to"""
import click

from dp_cli.output import ok, error
from dp_cli.commands._utils import session_option, _get_page, resolve_locator


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
    @click.option('--locator', default=None, help='滚动特定元素（而非页面）')
    @session_option
    def cmd_scroll(x, y, locator, session):
        """滚动页面或元素。

        \b
        示例:
          dp scroll --y 300
          dp scroll --y -200
          dp scroll --locator "css:.scroll-container" --y 100
        """
        page = _get_page(session)
        try:
            if locator:
                ele = page.ele(locator)
                ele.scroll.down(y) if y > 0 else ele.scroll.up(abs(y))
            else:
                if y > 0:
                    page.scroll.down(y)
                elif y < 0:
                    page.scroll.up(abs(y))
                if x > 0:
                    page.scroll.right(x)
                elif x < 0:
                    page.scroll.left(abs(x))
            ok({'x': x, 'y': y}, msg='滚动成功')
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
