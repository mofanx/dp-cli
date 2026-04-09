# -*- coding:utf-8 -*-
"""页面操作命令: screenshot / pdf / eval / add-init-js / dialog-accept / dialog-dismiss / wait"""
import json
from pathlib import Path
from time import perf_counter, sleep as _sleep

import click

from dp_cli.output import ok, error, format_page_info
from dp_cli.commands._utils import session_option, _get_page


def register(cli):

    @cli.command('screenshot')
    @session_option
    @click.option('--locator', default=None, help='截图特定元素')
    @click.option('--filename', default=None, help='保存路径')
    @click.option('--full-page', is_flag=True, help='截取完整页面（包括视口外）')
    @click.option('--format', 'fmt', type=click.Choice(['png', 'jpg', 'jpeg']),
                  default='png', show_default=True)
    def cmd_screenshot(session, locator, filename, full_page, fmt):
        """截图。DrissionPage 支持完整页面截图（含视口外内容）。

        \b
        示例:
          dp screenshot
          dp screenshot --filename page.png
          dp screenshot --full-page
          dp screenshot --locator "#header"
        """
        page = _get_page(session)
        try:
            if not filename:
                from datetime import datetime
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f'screenshot_{ts}.{fmt}'
            if locator:
                ele = page.ele(locator)
                if not ele or ele.__class__.__name__ == 'NoneElement':
                    error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                    return
                ele.get_screenshot(path=filename)
            else:
                page.get_screenshot(path=filename, full_page=full_page)
            ok({'filename': str(Path(filename).absolute())}, msg='截图已保存')
        except Exception as e:
            error(f'截图失败', code='SCREENSHOT_FAILED', detail=str(e))

    @cli.command('pdf')
    @session_option
    @click.option('--filename', default=None, help='保存路径')
    def cmd_pdf(session, filename):
        """将当前页面保存为 PDF。

        \b
        示例:
          dp pdf
          dp pdf --filename output.pdf
        """
        page = _get_page(session)
        try:
            if not filename:
                from datetime import datetime
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f'page_{ts}.pdf'
            result = page.run_cdp('Page.printToPDF', transferMode='ReturnAsBase64')
            import base64
            Path(filename).write_bytes(base64.b64decode(result['data']))
            ok({'filename': str(Path(filename).absolute())}, msg='PDF 已保存')
        except Exception as e:
            error(f'保存 PDF 失败', code='PDF_FAILED', detail=str(e))

    @cli.command('eval')
    @click.argument('script')
    @session_option
    @click.option('--locator', default=None, help='在特定元素上执行（this 指向该元素）')
    @click.option('--timeout', default=30, help='执行超时秒数', show_default=True)
    def cmd_eval(script, session, locator, timeout):
        """执行 JavaScript 并返回结果。

        \b
        示例:
          dp eval "document.title"
          dp eval "window.innerWidth"
          dp eval "el => el.textContent" --locator "#header"
          dp eval "return Array.from(document.links).map(l=>l.href)"
        """
        page = _get_page(session)
        try:
            if locator:
                ele = page.ele(locator, timeout=timeout)
                if not ele or ele.__class__.__name__ == 'NoneElement':
                    error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                    return
                if script.strip().startswith(('el =>', 'el=>', 'function')):
                    result = ele.run_js(f'return ({script})(this)', timeout=timeout)
                else:
                    result = ele.run_js(script, as_expr=True, timeout=timeout)
            else:
                if script.strip().startswith(('return ', 'function')):
                    result = page.run_js(script, timeout=timeout)
                else:
                    result = page.run_js(script, as_expr=True, timeout=timeout)
            ok({'result': result})
        except Exception as e:
            error(f'JavaScript 执行失败', code='JS_FAILED', detail=str(e))

    @cli.command('add-init-js')
    @click.argument('script')
    @session_option
    def add_init_js(script, session):
        """添加在每个新页面加载前执行的 JS 脚本（反检测/环境修改等）。

        \b
        示例:
          dp add-init-js "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
          dp add-init-js "window.__dp_cli = true"
        """
        page = _get_page(session)
        try:
            js_id = page.add_init_js(script)
            ok({'id': js_id, 'script': script[:100]}, msg='初始化脚本已添加')
        except Exception as e:
            error(f'添加初始化脚本失败', code='INIT_JS_FAILED', detail=str(e))

    @cli.command('dialog-accept')
    @click.argument('text', required=False)
    @session_option
    def dialog_accept(text, session):
        """接受（确认）对话框。可选传入 prompt 输入内容。

        \b
        示例:
          dp dialog-accept
          dp dialog-accept "确认文本"
        """
        page = _get_page(session)
        try:
            page.handle_alert(accept=True, send=text)
            ok(msg='对话框已接受')
        except Exception as e:
            error(f'处理对话框失败', code='DIALOG_FAILED', detail=str(e))

    @cli.command('dialog-dismiss')
    @session_option
    def dialog_dismiss(session):
        """取消对话框。"""
        page = _get_page(session)
        try:
            page.handle_alert(accept=False)
            ok(msg='对话框已取消')
        except Exception as e:
            error(f'处理对话框失败', code='DIALOG_FAILED', detail=str(e))

    @cli.command('wait')
    @session_option
    @click.option('--url', default=None, help='等待 URL 变为此值（支持子串匹配）')
    @click.option('--locator', default=None, help='等待元素出现')
    @click.option('--locator-gone', default=None, help='等待元素消失')
    @click.option('--text', default=None, help='等待页面包含指定文本')
    @click.option('--loaded', is_flag=True, help='等待页面加载完成')
    @click.option('--timeout', default=30, help='超时秒数', show_default=True)
    def wait(session, url, locator, locator_gone, text, loaded, timeout):
        """等待条件满足。

        \b
        示例:
          dp wait --loaded
          dp wait --locator "#result"
          dp wait --url "success"
          dp wait --text "操作成功"
          dp wait --locator-gone "css:.loading"
        """
        page = _get_page(session)
        try:
            if loaded:
                page.wait.doc_loaded()
                ok(format_page_info(page), msg='页面已加载')
            elif url:
                page.wait.url_change(url, timeout=timeout)
                ok(format_page_info(page), msg='URL 已变化')
            elif locator:
                ele = page.wait.ele_displayed(locator, timeout=timeout)
                ok({'locator': locator, 'found': bool(ele)}, msg='元素已出现')
            elif locator_gone:
                page.wait.ele_hidden(locator_gone, timeout=timeout)
                ok({'locator': locator_gone}, msg='元素已消失')
            elif text:
                end = perf_counter() + timeout
                found = False
                while perf_counter() < end:
                    if text in page.html:
                        found = True
                        break
                    _sleep(0.3)
                if found:
                    ok({'text': text}, msg='文本已出现')
                else:
                    error(f'等待超时：文本未出现 "{text}"', code='WAIT_TIMEOUT')
            else:
                error('请至少指定一个等待条件', code='INVALID_ARGS')
        except Exception as e:
            error(f'等待失败', code='WAIT_FAILED', detail=str(e))
