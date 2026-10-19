# -*- coding:utf-8 -*-
"""元素交互命令: click / dblclick / fill / clear / select / hover / drag / check / upload / count"""
import click

from dp_cli.output import ok, error
from dp_cli.commands._utils import (session_option, frame_option,
                                    _get_page, resolve_locator, frame_ctx,
                                    _wait_ele)
from dp_cli.human import human_options, human_click, human_type, human_clear


def register(cli):

    _MOD_MASK = {'alt': 1, 'control': 2, 'ctrl': 2, 'meta': 4,
                 'cmd': 4, 'command': 4, 'shift': 8}

    def _cdp_click(page, x, y, button, modifiers, click_count):
        mask = 0
        for m in (modifiers or '').split(','):
            m = m.strip().lower()
            if not m:
                continue
            if m not in _MOD_MASK:
                error(f'未知修饰键: {m}（可选 Alt,Control,Meta,Shift）',
                      code='INVALID_ARGS')
                return False
            mask |= _MOD_MASK[m]
        btn_bits = {'left': 1, 'right': 2, 'middle': 4}
        bits = btn_bits.get(button, 1)
        try:
            page.run_cdp('Input.dispatchMouseEvent', type='mouseMoved',
                         x=x, y=y, buttons=0)
            for i in range(click_count):
                page.run_cdp('Input.dispatchMouseEvent',
                             type='mousePressed', x=x, y=y, button=button,
                             clickCount=i + 1, buttons=bits, modifiers=mask)
                page.run_cdp('Input.dispatchMouseEvent',
                             type='mouseReleased', x=x, y=y, button=button,
                             clickCount=i + 1, buttons=0, modifiers=mask)
            return True
        except Exception as e:
            error('CDP 点击失败', code='CLICK_FAILED', detail=str(e))
            return False

    @cli.command('click')
    @click.argument('locator')
    @session_option
    @click.option('--index', default=1, help='第几个匹配元素', show_default=True)
    @click.option('--by-js', is_flag=True, help='使用 JavaScript 点击')
    @click.option('--button', type=click.Choice(['left', 'right', 'middle']),
                  default='left', show_default=True, help='鼠标按键')
    @click.option('--modifiers', default=None,
                  help='修饰键组合，逗号分隔：Alt,Control,Meta,Shift')
    @click.option('--position', default=None,
                  help='元素内点击坐标 x,y（默认中心）')
    @click.option('--click-count', type=int, default=1, show_default=True,
                  help='点击次数（2=双击）')
    @click.option('--force', is_flag=True, help='跳过 actionability 检查')
    @click.option('--no-wait', is_flag=True, help='不等待可交互，立即执行')
    @human_options
    @frame_option
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def cmd_click(locator, session, index, by_js, button, modifiers, position,
                  click_count, force, no_wait, human, human_min, human_max,
                  frame, timeout):
        """点击元素（默认等待可见/稳定/无遮挡，--force 跳过）。

        \b
        示例:
          dpx click "text:登录"
          dpx click "#submit-btn"
          dpx click "ref:5"              # 使用快照编号
          dpx click "css:.btn-primary" --by-js
          dpx click "css:li" --index 3
          dpx click "ref:5" --human      # 拟人：贝塞尔轨迹+随机停顿
          dpx click "#btn" --button right --modifiers Shift
          dpx click "#btn" --position 10,5 --click-count 2
        """
        advanced = any([button != 'left', modifiers, position, click_count > 1])
        if human and (by_js or advanced):
            error('--human 与 --by-js/--button/--modifiers/--position/--click-count 互斥',
                  code='INVALID_ARGS')
            return
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        ctx = frame_ctx(page, frame, session)
        try:
            if force or by_js:
                ele = _wait_ele(ctx, locator, index=index, timeout=timeout,
                                no_wait=no_wait, hit_check=False,
                                code='CLICK_FAILED', label='点击')
            else:
                ele = _wait_ele(ctx, locator, index=index, timeout=timeout,
                                no_wait=no_wait, code='CLICK_FAILED',
                                label='点击')
            if ele is None:
                return
            if by_js:
                ele.click.js()
            elif human:
                human_click(page, ele, human_min, human_max)
            elif advanced or force:
                r = ele.rect
                x = r.location[0] + (r.size[0] / 2)
                y = r.location[1] + (r.size[1] / 2)
                if position:
                    try:
                        px, py = (float(v) for v in position.split(','))
                    except ValueError:
                        error('--position 格式应为 x,y', code='INVALID_ARGS')
                        return
                    x, y = r.location[0] + px, r.location[1] + py
                if not _cdp_click(page, x, y, button, modifiers, click_count):
                    return
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
    @click.option('--no-wait', is_flag=True, help='不等待可交互，立即执行')
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def dblclick(locator, session, index, frame, timeout, no_wait,
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
            ele = _wait_ele(ctx, locator, index=index, timeout=timeout,
                            no_wait=no_wait, code='DBLCLICK_FAILED', label='双击')
            if ele is None:
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
    @click.option('--no-wait', is_flag=True, help='不等待可交互，立即执行')
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def fill(locator, value, session, index, clear, by_js, frame, timeout, no_wait,
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
            ele = _wait_ele(ctx, locator, index=index, timeout=timeout,
                            no_wait=no_wait, code='FILL_FAILED', label='填入')
            if ele is None:
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
    @click.option('--no-wait', is_flag=True, help='不等待可交互，立即执行')
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def cmd_clear(locator, session, index, frame, timeout, no_wait):
        """清空输入框内容。"""
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        ctx = frame_ctx(page, frame, session)
        try:
            ele = _wait_ele(ctx, locator, index=index, timeout=timeout,
                            no_wait=no_wait, code='CLEAR_FAILED', label='清空')
            if ele is None:
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
    @click.option('--no-wait', is_flag=True, help='不等待可交互，立即执行')
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def cmd_select(locator, value, session, index, by_text, sel_by_index, frame, timeout, no_wait):
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
            ele = _wait_ele(ctx, locator, index=index, timeout=timeout,
                            no_wait=no_wait, code='SELECT_FAILED', label='选择')
            if ele is None:
                return
            if sel_by_index is not None:
                ele.select.by_index(sel_by_index)
            elif by_text:
                ele.select.by_text(value)
            else:
                ele.select.by_value(value)
            ok({'locator': locator, 'value': value}, msg='选择成功')
        except Exception as e:
            # 失败时枚举可用 option（agent 自查 next-step 不需要第二轮探测）
            detail = str(e)
            try:
                opts = ele.eles('tag:option') if ele else []
                if opts:
                    avail = []
                    for o in opts[:50]:
                        v = o.attr('value')
                        t = (o.raw_text or '').strip()[:40]
                        avail.append(f'{v}({t})' if v else t)
                    if avail:
                        detail += '。可用选项: ' + ', '.join(avail)
            except Exception:
                pass
            error(f'选择失败: {locator}', code='SELECT_FAILED', detail=detail)

    @cli.command('hover')
    @click.argument('locator')
    @session_option
    @click.option('--index', default=1, help='第几个匹配元素', show_default=True)
    @click.option('--offset-x', default=None, type=int, help='X 偏移量（像素）')
    @click.option('--offset-y', default=None, type=int, help='Y 偏移量（像素）')
    @frame_option
    @click.option('--no-wait', is_flag=True, help='不等待可交互，立即执行')
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def hover(locator, session, index, offset_x, offset_y, frame, timeout, no_wait):
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
            ele = _wait_ele(ctx, locator, index=index, timeout=timeout,
                            no_wait=no_wait, code='HOVER_FAILED', label='悬停')
            if ele is None:
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
    @click.option('--no-wait', is_flag=True, help='不等待可交互，立即执行')
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
    @click.option('--no-wait', is_flag=True, help='不等待可交互，立即执行')
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def cmd_check(locator, session, check, frame, timeout, no_wait):
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
            ele = _wait_ele(ctx, locator, timeout=timeout,
                            no_wait=no_wait, code='CHECK_FAILED', label='checkbox 操作')
            if ele is None:
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
    @click.option('--no-wait', is_flag=True, help='不等待可交互，立即执行')
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def upload(locator, file_path, session, frame, timeout, no_wait):
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
            ele = _wait_ele(ctx, locator, timeout=timeout,
                            no_wait=no_wait, code='UPLOAD_FAILED', label='文件上传')
            if ele is None:
                return
            ele.input(file_path)
            ok({'locator': locator, 'file': file_path}, msg='文件上传成功')
        except Exception as e:
            error(f'文件上传失败', code='UPLOAD_FAILED', detail=str(e))

    @cli.command('fill-form')
    @click.argument('pairs', nargs=-1)
    @session_option
    @click.option('--data', 'data_json', default=None,
                  help='JSON 对象 {"locator":"value",...}（与位置参数互斥）')
    @click.option('--no-wait', is_flag=True, help='不等待可交互，立即执行')
    @frame_option
    @click.option('--timeout', default=10, help='每字段等待超时秒数',
                  show_default=True)
    def fill_form(pairs, session, data_json, no_wait, frame, timeout):
        """批量表单填充：逐字段 actionability 等待 + fill，per-field 明细。

        \b
        示例:
          dpx fill-form --data '{"#user":"admin","@name=pwd":"123"}'
          dpx fill-form "#user=admin" "css:#pwd=123"
          dpx fill-form --data @form.json     # 支持 @file 读 JSON
        """
        import json as _json
        fields = {}
        if data_json and pairs:
            error('--data 与位置参数互斥', code='INVALID_ARGS')
            return
        if data_json:
            src = data_json
            if src.startswith('@'):
                try:
                    from pathlib import Path as _P
                    src = _P(src[1:]).read_text(encoding='utf-8')
                except OSError as e:
                    error(f'读取 --data 文件失败: {e}', code='INVALID_ARGS')
                    return
            try:
                fields = _json.loads(src)
            except _json.JSONDecodeError as e:
                error(f'--data JSON 解析失败: {e}', code='INVALID_JSON')
                return
            if not isinstance(fields, dict):
                error('--data 须为 JSON 对象', code='INVALID_JSON')
                return
        elif pairs:
            for p in pairs:
                if '=' not in p:
                    error(f'位置参数须为 locator=value: {p}',
                          code='INVALID_ARGS')
                    return
                k, v = p.split('=', 1)
                fields[k] = v
        else:
            error('请提供 --data 或 locator=value 参数', code='INVALID_ARGS')
            return
        page = _get_page(session)
        ctx = frame_ctx(page, frame, session)
        results = []
        n_fail = 0
        from dp_cli.commands._utils import wait_actionable as _wa
        import io as _io, contextlib as _cl
        for loc, val in fields.items():
            try:
                # resolve_locator 内部 error() 打 JSON 并 sys.exit——
                # 重定向捕获避免污染 stdout 契约，失败计入 per-field
                _buf = _io.StringIO()
                with _cl.redirect_stdout(_buf):
                    real = resolve_locator(loc, session)
                if no_wait:
                    ele = ctx.ele(real, timeout=0)
                    fail = ({'reason': 'not_found'}
                            if ele is None
                            or ele.__class__.__name__ == 'NoneElement'
                            else None)
                else:
                    ele, fail = _wa(ctx, real, timeout=timeout,
                                    hit_check=False)
                if fail is not None:
                    results.append({'locator': loc, 'ok': False,
                                    'reason': fail.get('reason', 'failed')})
                    n_fail += 1
                    continue
                ele.input(val, clear=True)
                results.append({'locator': loc, 'ok': True})
            except SystemExit:
                try:
                    reason = _json.loads(
                        _buf.getvalue()
                    ).get('message', 'resolve_error')[:120]
                except Exception:
                    reason = 'resolve_error'
                results.append({'locator': loc, 'ok': False,
                                'reason': reason})
                n_fail += 1
            except Exception as e:
                results.append({'locator': loc, 'ok': False,
                                'reason': str(e)[:120]})
                n_fail += 1
        data = {'total': len(fields), 'filled': len(fields) - n_fail,
                'failed': n_fail, 'fields': results}
        if n_fail:
            data['status'] = 'error'
            data['code'] = 'FILL_FAILED'
            data['message'] = f'{n_fail}/{len(fields)} 字段填入失败'
            print(_json.dumps(data, ensure_ascii=False))
            raise SystemExit(1)
        ok(data, msg=f'已填入 {len(fields)} 个字段')

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
