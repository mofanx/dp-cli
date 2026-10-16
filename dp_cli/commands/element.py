# -*- coding:utf-8 -*-
"""元素交互命令: click / dblclick / fill / clear / select / hover / drag / check / upload / count"""
import click

from dp_cli.output import ok, error
from dp_cli.commands._utils import (session_option, frame_option,
                                    _get_page, resolve_locator, frame_ctx)
from dp_cli.human import human_options, human_click, human_type, human_clear


def register(cli):

    @cli.command('click')
    @click.argument('locator')
    @session_option
    @click.option('--index', default=1, help='第几个匹配元素', show_default=True)
    @click.option('--by-js', is_flag=True, help='使用 JavaScript 点击')
    @human_options
    @frame_option
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def cmd_click(locator, session, index, by_js, frame, timeout,
                  human, human_min, human_max):
        """点击元素。

        \b
        示例:
          dpx click "text:登录"
          dpx click "#submit-btn"
          dpx click "ref:5"              # 使用快照编号
          dpx click "css:.btn-primary" --by-js
          dpx click "css:li" --index 3
          dpx click "ref:5" --human      # 拟人：贝塞尔轨迹+随机停顿
        """
        if human and by_js:
            error('--human 与 --by-js 互斥（JS 点击无轨迹可言）',
                  code='INVALID_ARGS')
            return
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        ctx = frame_ctx(page, frame, session)
        try:
            ele = ctx.ele(locator, index=index, timeout=timeout)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                return
            if by_js:
                ele.click.js()
            elif human:
                human_click(page, ele, human_min, human_max)
            else:
                ele.click()
            ok({'locator': locator}, msg='点击成功')
        except Exception as e:
            error(f'点击失败: {locator}', code='CLICK_FAILED', detail=str(e))

    @cli.command('dblclick')
    @click.argument('locator')
    @session_option
    @click.option('--index', default=1, help='第几个匹配元素', show_default=True)
    @human_options
    @frame_option
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def dblclick(locator, session, index, frame, timeout,
                 human, human_min, human_max):
        """双击元素。

        \b
        示例:
          dpx dblclick "#editable-cell"
          dpx dblclick "ref:5"
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        ctx = frame_ctx(page, frame, session)
        try:
            ele = ctx.ele(locator, index=index, timeout=timeout)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                return
            if human:
                human_click(page, ele, human_min, human_max, times=2)
            else:
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
    @human_options
    @frame_option
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def fill(locator, value, session, index, clear, by_js, frame, timeout,
             human, human_min, human_max):
        """向输入框填入文本。

        \b
        示例:
          dpx fill "@name=username" admin
          dpx fill "ref:15" "Python"     # 使用快照编号
          dpx fill "css:textarea" "多行\\n文本"
          dpx fill "ref:15" "secret" --human   # 拟人：点击聚焦+逐字符随机间隔
        """
        if human and by_js:
            error('--human 与 --by-js 互斥', code='INVALID_ARGS')
            return
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        ctx = frame_ctx(page, frame, session)
        try:
            ele = ctx.ele(locator, index=index, timeout=timeout)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                return
            if human:
                human_click(page, ele, human_min, human_max)
                if clear:
                    human_clear(page)
                human_type(page, value, human_min, human_max)
            else:
                ele.input(value, clear=clear, by_js=by_js)
            ok({'locator': locator, 'value': value}, msg='填入成功')
        except Exception as e:
            error(f'填入失败: {locator}', code='FILL_FAILED', detail=str(e))

    @cli.command('clear')
    @click.argument('locator')
    @session_option
    @click.option('--index', default=1, help='第几个匹配元素', show_default=True)
    @frame_option
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def cmd_clear(locator, session, index, frame, timeout):
        """清空输入框内容。"""
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        ctx = frame_ctx(page, frame, session)
        try:
            ele = ctx.ele(locator, index=index, timeout=timeout)
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
    @frame_option
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def cmd_select(locator, value, session, index, by_text, sel_by_index, frame, timeout):
        """选择下拉框选项。

        \b
        示例:
          dpx select "@name=city" beijing
          dpx select "ref:12" admin          # 用快照编号
          dpx select "#size" "" --by-index 2
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        ctx = frame_ctx(page, frame, session)
        try:
            ele = ctx.ele(locator, index=index, timeout=timeout)
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
    @frame_option
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def hover(locator, session, index, offset_x, offset_y, frame, timeout):
        """悬停鼠标到元素。

        \b
        示例:
          dpx hover "css:.menu-item"
          dpx hover "ref:8"                   # 用快照编号
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        ctx = frame_ctx(page, frame, session)
        try:
            ele = ctx.ele(locator, index=index, timeout=timeout)
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
    @frame_option
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def drag(from_locator, to_locator, session, duration, frame, timeout):
        """拖拽元素到另一个元素。

        \b
        示例:
          dpx drag "#draggable" "#droptarget"
          dpx drag "ref:3" "ref:7"          # 用快照编号
        """
        from_locator = resolve_locator(from_locator, session)
        to_locator = resolve_locator(to_locator, session)
        page = _get_page(session)
        ctx = frame_ctx(page, frame, session)
        try:
            src = ctx.ele(from_locator, timeout=timeout)
            dst = ctx.ele(to_locator, timeout=timeout)
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
    @frame_option
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def cmd_check(locator, session, check, frame, timeout):
        """勾选或取消勾选 checkbox/radio。

        \b
        示例:
          dpx check "#agree-terms"
          dpx check "ref:10"                  # 用快照编号
          dpx check "@name=remember" --uncheck
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        ctx = frame_ctx(page, frame, session)
        try:
            ele = ctx.ele(locator, timeout=timeout)
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
    @frame_option
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def upload(locator, file_path, session, frame, timeout):
        """上传文件到 input[type=file] 元素。

        \b
        示例:
          dpx upload "@name=avatar" /path/to/image.png
          dpx upload "ref:15" ./document.pdf   # 用快照编号
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        ctx = frame_ctx(page, frame, session)
        try:
            ele = ctx.ele(locator, timeout=timeout)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                return
            ele.input(file_path)
            ok({'locator': locator, 'file': file_path}, msg='文件上传成功')
        except Exception as e:
            error(f'文件上传失败', code='UPLOAD_FAILED', detail=str(e))

    @cli.command('count')
    @click.argument('locator')
    @session_option
    @frame_option
    @click.option('--timeout', default=0, type=float, help='等待元素出现的超时秒数（默认 0 即时返回）', show_default=True)
    def cmd_count(locator, session, frame, timeout):
        """统计匹配选择器的元素数量。

        \b
        懒加载检测工作流:
          dpx count ".item"              → 20
          dpx scroll --bottom && dpx wait --idle
          dpx count ".item"              → 40
          dpx scroll --bottom && dpx wait --idle
          dpx count ".item"              → 40  (不变，加载完毕)

        \b
        支持的选择器（可省略 css:/xpath: 前缀）:
          dpx count ".card"              → css（自动识别 . 开头）
          dpx count "#list li"           → css（自动识别 # 开头）
          dpx count "div.item"           → css（自动识别 tag.class）
          dpx count "a[href]"            → css（自动识别 [attr]）
          dpx count "//ul/li"            → xpath（自动识别 // 开头）
          dpx count "tag:img"            → DrissionPage 原生语法
          dpx count "css:tr"             → 显式 css 前缀
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        ctx = frame_ctx(page, frame, session)
        try:
            eles = ctx.eles(locator, timeout=timeout)
            count = len(eles)
            ok({'locator': locator, 'count': count}, msg=f'找到 {count} 个元素')
        except Exception as e:
            error(f'统计失败', code='COUNT_FAILED', detail=str(e))
