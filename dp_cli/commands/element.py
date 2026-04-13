# -*- coding:utf-8 -*-
"""元素交互命令: click / dblclick / fill / clear / select / hover / drag / check / upload"""
import click

from dp_cli.output import ok, error
from dp_cli.commands._utils import session_option, _get_page, resolve_locator


def register(cli):

    @cli.command('click')
    @click.argument('locator')
    @session_option
    @click.option('--index', default=1, help='第几个匹配元素', show_default=True)
    @click.option('--by-js', is_flag=True, help='使用 JavaScript 点击')
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def cmd_click(locator, session, index, by_js, timeout):
        """点击元素。

        \b
        示例:
          dp click "text:登录"
          dp click "#submit-btn"
          dp click "ref:5"              # 使用快照编号
          dp click "css:.btn-primary" --by-js
          dp click "css:li" --index 3
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        try:
            ele = page.ele(locator, index=index, timeout=timeout)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                return
            if by_js:
                ele.click.js()
            else:
                ele.click()
            ok({'locator': locator}, msg='点击成功')
        except Exception as e:
            error(f'点击失败: {locator}', code='CLICK_FAILED', detail=str(e))

    @cli.command('dblclick')
    @click.argument('locator')
    @session_option
    @click.option('--index', default=1, help='第几个匹配元素', show_default=True)
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def dblclick(locator, session, index, timeout):
        """双击元素。

        \b
        示例:
          dp dblclick "#editable-cell"
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        try:
            ele = page.ele(locator, index=index, timeout=timeout)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                return
            ele.click(count=2)
            ok({'locator': locator}, msg='双击成功')
        except Exception as e:
            error(f'双击失败: {locator}', code='CLICK_FAILED', detail=str(e))

    @cli.command('fill')
    @click.argument('locator')
    @click.argument('value')
    @session_option
    @click.option('--index', default=1, help='第几个匹配元素', show_default=True)
    @click.option('--clear', is_flag=True, default=True, help='填入前清空（默认开启）')
    @click.option('--by-js', is_flag=True, help='使用 JavaScript 设置值')
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def fill(locator, value, session, index, clear, by_js, timeout):
        """向输入框填入文本。

        \b
        示例:
          dp fill "@name=username" admin
          dp fill "ref:15" "Python"     # 使用快照编号
          dp fill "css:textarea" "多行\\n文本"
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        try:
            ele = page.ele(locator, index=index, timeout=timeout)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                return
            ele.input(value, clear=clear, by_js=by_js)
            ok({'locator': locator, 'value': value}, msg='填入成功')
        except Exception as e:
            error(f'填入失败: {locator}', code='FILL_FAILED', detail=str(e))

    @cli.command('clear')
    @click.argument('locator')
    @session_option
    @click.option('--index', default=1, help='第几个匹配元素', show_default=True)
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def cmd_clear(locator, session, index, timeout):
        """清空输入框内容。"""
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        try:
            ele = page.ele(locator, index=index, timeout=timeout)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                return
            ele.clear()
            ok({'locator': locator}, msg='清空成功')
        except Exception as e:
            error(f'清空失败: {locator}', code='CLEAR_FAILED', detail=str(e))

    @cli.command('select')
    @click.argument('locator')
    @click.argument('value')
    @session_option
    @click.option('--index', default=1, help='第几个匹配 select 元素', show_default=True)
    @click.option('--by-text', is_flag=True, help='按文本选择（默认按 value）')
    @click.option('--by-index', 'sel_by_index', default=None, type=int, help='按位置索引选择（从1开始）')
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def cmd_select(locator, value, session, index, by_text, sel_by_index, timeout):
        """选择下拉框选项。

        \b
        示例:
          dp select "@name=city" beijing
          dp select "css:select#role" admin --by-text
          dp select "#size" "" --by-index 2
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        try:
            ele = page.ele(locator, index=index, timeout=timeout)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                return
            if sel_by_index is not None:
                ele.select.by_index(sel_by_index)
            elif by_text:
                ele.select.by_text(value)
            else:
                ele.select.by_value(value)
            ok({'locator': locator, 'value': value}, msg='选择成功')
        except Exception as e:
            error(f'选择失败: {locator}', code='SELECT_FAILED', detail=str(e))

    @cli.command('hover')
    @click.argument('locator')
    @session_option
    @click.option('--index', default=1, help='第几个匹配元素', show_default=True)
    @click.option('--offset-x', default=None, type=int, help='X 偏移量（像素）')
    @click.option('--offset-y', default=None, type=int, help='Y 偏移量（像素）')
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def hover(locator, session, index, offset_x, offset_y, timeout):
        """悬停鼠标到元素。

        \b
        示例:
          dp hover "css:.menu-item"
          dp hover "#tooltip-trigger" --offset-x 10 --offset-y 5
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        try:
            ele = page.ele(locator, index=index, timeout=timeout)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                return
            ele.hover(offset_x=offset_x, offset_y=offset_y)
            ok({'locator': locator}, msg='悬停成功')
        except Exception as e:
            error(f'悬停失败: {locator}', code='HOVER_FAILED', detail=str(e))

    @cli.command('drag')
    @click.argument('from_locator')
    @click.argument('to_locator')
    @session_option
    @click.option('--duration', default=0.5, help='拖拽持续时间（秒）', show_default=True)
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def drag(from_locator, to_locator, session, duration, timeout):
        """拖拽元素到另一个元素。

        \b
        示例:
          dp drag "#draggable" "#droptarget"
          dp drag "css:.drag-item" "css:.drop-zone" --duration 1.0
        """
        from_locator = resolve_locator(from_locator, session)
        to_locator = resolve_locator(to_locator, session)
        page = _get_page(session)
        try:
            src = page.ele(from_locator, timeout=timeout)
            dst = page.ele(to_locator, timeout=timeout)
            if not src or src.__class__.__name__ == 'NoneElement':
                error(f'未找到源元素: {from_locator}', code='ELEMENT_NOT_FOUND')
                return
            if not dst or dst.__class__.__name__ == 'NoneElement':
                error(f'未找到目标元素: {to_locator}', code='ELEMENT_NOT_FOUND')
                return
            src.drag_to(dst, duration=duration)
            ok({'from': from_locator, 'to': to_locator}, msg='拖拽成功')
        except Exception as e:
            error(f'拖拽失败', code='DRAG_FAILED', detail=str(e))

    @cli.command('check')
    @click.argument('locator')
    @session_option
    @click.option('--check/--uncheck', default=True, help='选中/取消选中')
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def cmd_check(locator, session, check, timeout):
        """勾选或取消勾选 checkbox/radio。

        \b
        示例:
          dp check "#agree-terms"
          dp check "@name=remember" --uncheck
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        try:
            ele = page.ele(locator, timeout=timeout)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                return
            current = ele.states.is_checked
            if (check and not current) or (not check and current):
                ele.click()
            ok({'locator': locator, 'checked': check}, msg='操作成功')
        except Exception as e:
            error(f'checkbox 操作失败: {locator}', code='CHECK_FAILED', detail=str(e))

    @cli.command('upload')
    @click.argument('locator')
    @click.argument('file_path')
    @session_option
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def upload(locator, file_path, session, timeout):
        """上传文件到 input[type=file] 元素。

        \b
        示例:
          dp upload "@name=avatar" /path/to/image.png
          dp upload "css:input[type=file]" ./document.pdf
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        try:
            ele = page.ele(locator, timeout=timeout)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                return
            ele.input(file_path)
            ok({'locator': locator, 'file': file_path}, msg='文件上传成功')
        except Exception as e:
            error(f'文件上传失败', code='UPLOAD_FAILED', detail=str(e))
