# -*- coding:utf-8 -*-
"""
dp-cli —— DrissionPage 命令行工具
比 playwright-cli 更强大，充分利用 DrissionPage 的独特优势：
  - 不基于 webdriver，天然反检测
  - 支持浏览器模式 + HTTP 模式无缝切换
  - 强大的定位语法（比 a11y ref 更稳定）
  - lxml 高效批量解析，snapshot 一次 CDP 调用
  - 支持 shadow-root / iframe 穿透
  - 内置网络包监听能力
"""
import json
import sys
from pathlib import Path

import click

from dp_cli.session import (get_browser, close_browser, list_sessions,
                            delete_session, load_session, save_session)
from dp_cli.output import ok, error, format_page_info
from dp_cli.snapshot import (take_snapshot, render_snapshot_text,
                             extract_structured, query_elements)

CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'],
                         max_content_width=100)


# ─────────────────────────────────────────────
# 全局选项装饰器
# ─────────────────────────────────────────────
def session_option(f):
    return click.option('-s', '--session', default='default',
                        help='会话名称，默认 default', show_default=True)(f)


def _get_page(session: str):
    """获取页面对象，失败则 error 退出"""
    try:
        return get_browser(session)
    except Exception as e:
        error(f'无法连接浏览器会话 [{session}]，请先执行 dp open',
              code='SESSION_NOT_FOUND', detail=str(e))


def _records_to_csv(records: list) -> str:
    """将记录列表转为 CSV 字符串（含 BOM，Excel 直接打开不乱码）"""
    import io, csv
    if not records:
        return ''
    fields = list(records[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction='ignore',
                            lineterminator='\n')
    writer.writeheader()
    for row in records:
        # 列表值转为逗号拼接字符串
        clean = {k: ('|'.join(str(i) for i in v) if isinstance(v, list) else v)
                 for k, v in row.items()}
        writer.writerow(clean)
    return buf.getvalue()


# ─────────────────────────────────────────────
# CLI 主入口
# ─────────────────────────────────────────────
@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.version_option(message='%(version)s')
@click.pass_context
def cli(ctx):
    """
    \b
    dp-cli —— DrissionPage 命令行工具
    完整文档: https://DrissionPage.cn/cli

    \b
    快速开始:
      dp open https://example.com
      dp snapshot
      dp click "text:登录"
      dp fill "@name=username" admin
      dp close
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ═══════════════════════════════════════════════
# 浏览器生命周期
# ═══════════════════════════════════════════════

@cli.command('open')
@click.argument('url', required=False)
@session_option
@click.option('--headless', is_flag=True, help='无头模式')
@click.option('--browser', 'browser_path', default=None, help='浏览器可执行文件路径')
@click.option('--profile', 'user_data_dir', default=None, help='用户数据目录')
@click.option('--proxy', default=None, help='代理服务器，如 http://127.0.0.1:7890')
@click.option('--port', type=int, default=None, help='连接指定端口的已有浏览器实例')
@click.option('--new', is_flag=True, help='强制创建新实例（不复用已有会话）')
def cmd_open(url, session, headless, browser_path, user_data_dir, proxy, port, new):
    """打开浏览器并可选导航到 URL。

    \b
    【复用用户自己的浏览器】(最常见场景，保留登录状态/Cookie/历史)
    第一步：用调试模式启动你自己的 Chrome/Chromium：
      google-chrome --remote-debugging-port=9222
      chromium --remote-debugging-port=9222 --user-data-dir=/home/you/.config/chromium
      # 也可连接已打开的浏览器（只需浏览器启动时带了 --remote-debugging-port）
    第二步：用 dp 接管：
      dp open --port 9222
      dp open https://example.com --port 9222
    第三步：后续命令无需再指定 --port（会话自动记住端口）：
      dp snapshot
      dp click "text:登录"

    \b
    【dp 自动管理浏览器】(全新隔离环境，每个会话独立)
      dp open
      dp open https://example.com
      dp open https://example.com --headless
      dp -s work open https://github.com
      dp open https://example.com --profile /home/you/.config/chrome  # 使用指定目录
    """
    if new:
        delete_session(session)

    try:
        page = get_browser(session, headless=headless, browser_path=browser_path,
                           user_data_dir=user_data_dir, proxy=proxy, port=port)
    except Exception as e:
        error(f'启动浏览器失败: {e}', code='BROWSER_START_FAILED', detail=str(e))
        return

    if url:
        try:
            page.get(url)
        except Exception as e:
            error(f'导航失败: {e}', code='NAVIGATE_FAILED', detail=str(e))
            return

    ok(format_page_info(page), msg='浏览器已就绪')


@cli.command()
@click.argument('url')
@session_option
@click.option('--timeout', default=30, help='超时秒数', show_default=True)
@click.option('--retry', default=3, help='重试次数', show_default=True)
def goto(url, session, timeout, retry):
    """导航到指定 URL。
    
    \b
    示例:
      dp goto https://example.com
      dp goto https://example.com --timeout 60
    """
    page = _get_page(session)
    try:
        page.get(url, timeout=timeout, retry=retry)
        ok(format_page_info(page))
    except Exception as e:
        error(f'导航到 {url} 失败', code='NAVIGATE_FAILED', detail=str(e))


@cli.command()
@session_option
def reload(session):
    """刷新当前页面。"""
    page = _get_page(session)
    try:
        page.get(page.url)
        ok(format_page_info(page))
    except Exception as e:
        error(f'刷新失败', code='RELOAD_FAILED', detail=str(e))


@cli.command('go-back')
@session_option
def go_back(session):
    """浏览器后退。"""
    page = _get_page(session)
    try:
        page.back()
        ok(format_page_info(page))
    except Exception as e:
        error('后退失败', code='NAVIGATE_FAILED', detail=str(e))


@cli.command('go-forward')
@session_option
def go_forward(session):
    """浏览器前进。"""
    page = _get_page(session)
    try:
        page.forward()
        ok(format_page_info(page))
    except Exception as e:
        error('前进失败', code='NAVIGATE_FAILED', detail=str(e))


@cli.command('close')
@session_option
@click.option('--del-data', is_flag=True, help='同时删除用户数据目录')
@click.option('--force', is_flag=True, help='强制关闭浏览器（user_connected 模式下默认只断开连接）')
def cmd_close(session, del_data, force):
    """关闭浏览器会话。

    如果是通过 --port 连接的用户自己的浏览器，默认只断开连接不关闭浏览器。
    用 --force 才会真正关闭浏览器进程。
    """
    sess = load_session(session)
    if not sess:
        error(f'会话 [{session}] 不存在', code='SESSION_NOT_FOUND')
        return

    user_connected = sess.get('user_connected', False)
    if user_connected and not force:
        # 用户自己的浏览器：只清除会话记录，不关闭浏览器
        delete_session(session)
        ok(msg=f'已断开与 [{session}] 的连接（浏览器仍运行）。用 --force 关闭浏览器。')
    else:
        result = close_browser(session, del_data=del_data)
        if result:
            ok(msg=f'会话 [{session}] 已关闭')
        else:
            error(f'关闭失败', code='CLOSE_FAILED')


@cli.command('close-all')
def close_all():
    """关闭所有会话。"""
    sessions = list_sessions()
    closed = []
    for s in sessions:
        close_browser(s['name'])
        closed.append(s['name'])
    ok({'closed': closed}, msg=f'已关闭 {len(closed)} 个会话')


@cli.command('list')
def cmd_list():
    """列出所有活跃会话。"""
    sessions = list_sessions()
    ok({'sessions': sessions, 'count': len(sessions)})


@cli.command('delete-data')
@session_option
def delete_data(session):
    """删除会话的用户数据目录。"""
    close_browser(session, del_data=True)
    ok(msg=f'会话 [{session}] 数据已删除')


# ═══════════════════════════════════════════════
# 页面快照
# ═══════════════════════════════════════════════

@cli.command()
@session_option
@click.option('--mode', type=click.Choice(['interactive', 'content', 'full', 'text']),
              default='interactive', show_default=True,
              help='快照模式：interactive=可交互元素, full=完整DOM树, text=纯文本')
@click.option('--selector', default=None, help='限定快照范围的定位器')
@click.option('--max-depth', default=8, help='full/content 模式最大深度', show_default=True)
@click.option('--min-text', default=2, help='content 模式：文本最短长度过滤', show_default=True)
@click.option('--max-text', default=500, help='content 模式：文本最长长度过滤', show_default=True)
@click.option('--format', 'fmt', type=click.Choice(['json', 'text']),
              default='text', show_default=True, help='输出格式')
@click.option('--filename', default=None, help='保存到文件路径')
def snapshot(session, mode, selector, max_depth, min_text, max_text, fmt, filename):
    """获取页面快照。DrissionPage 特有的高效多维快照。

    \b
    模式说明:
      interactive  只列出可交互元素及最优定位器（默认，AI 操控最佳）
      content      去噪内容树：自动过滤 script/style，只保留有文本的语义节点
                   适合快速了解页面有哪些内容，找到数据所在的 CSS 类名
      full         完整 DOM 树（lxml 高效解析，无需多次 CDP）
      text         页面纯文本内容

    \b
    示例:
      dp snapshot
      dp snapshot --mode content                    # 查看页面内容节点
      dp snapshot --mode content --selector "css:.job-list"  # 限定区域
      dp snapshot --mode full
      dp snapshot --mode text
      dp snapshot --format json
    """
    page = _get_page(session)
    try:
        data = take_snapshot(page, mode=mode, selector=selector,
                             max_depth=max_depth, min_text=min_text, max_text=max_text)
    except Exception as e:
        error(f'获取快照失败', code='SNAPSHOT_FAILED', detail=str(e))
        return

    if fmt == 'json':
        output = json.dumps({'status': 'ok', 'data': data}, ensure_ascii=False, indent=2)
    else:
        output = render_snapshot_text(data)

    if filename:
        Path(filename).write_text(output, encoding='utf-8')
        ok(msg=f'快照已保存到 {filename}')
    else:
        click.echo(output)


@cli.command('extract')
@session_option
@click.argument('container')
@click.argument('fields_json')
@click.option('--limit', default=100, help='最多提取多少条记录', show_default=True)
@click.option('--output', 'output_fmt', type=click.Choice(['json', 'csv']),
              default='json', show_default=True, help='输出格式')
@click.option('--filename', default=None, help='保存结果到文件')
def cmd_extract(session, container, fields_json, limit, output_fmt, filename):
    """批量提取结构化数据（列表页核心工具）。

    \b
    CONTAINER  容器元素的定位器（每个容器对应一条记录）
    FIELDS_JSON  字段映射 JSON 字符串

    \b
    字段映射格式:
      简单形式:  {"字段名": "子元素定位器"}
      完整形式:  {"字段名": {"selector": "...", "attr": "href", "multi": false}}

      selector  子元素定位器（相对于容器）
      attr      取属性值而非文本，如 "href"、"src"、"data-id"
      multi     true → 返回列表（匹配所有子元素）
      default   元素不存在时的默认值

    \b
    示例:
      dp extract "css:.job-card-wrapper" \\
        '{"title":"css:.job-name","salary":"css:.salary","company":"css:.company-name","tags":{"selector":"css:.tag","multi":true}}'

    \b
    示例 (通用链接列表):
      dp extract "css:ul.list > li" '{"text":"css:a","url":{"selector":"css:a","attr":"href"}}'

    \b
    先用 snapshot --mode content 了解页面结构，再用 extract 定位容器和字段。
    """
    page = _get_page(session)
    try:
        fields = json.loads(fields_json)
    except json.JSONDecodeError as e:
        error(f'fields_json 格式错误: {e}', code='INVALID_JSON')
        return

    try:
        results = extract_structured(page, container, fields, limit=limit)
        data = {'count': len(results), 'records': results}
        if output_fmt == 'csv' or (filename and filename.endswith('.csv')):
            content = _records_to_csv(results)
            if filename:
                Path(filename).write_text(content, encoding='utf-8-sig')
                ok(data, msg=f'已提取 {len(results)} 条记录，保存到 {filename}')
            else:
                click.echo(content)
        elif filename:
            Path(filename).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'
            )
            ok(data, msg=f'已提取 {len(results)} 条记录，保存到 {filename}')
        else:
            ok(data, msg=f'已提取 {len(results)} 条记录')
    except Exception as e:
        error(f'提取失败', code='EXTRACT_FAILED', detail=str(e))


@cli.command('query')
@session_option
@click.argument('selector')
@click.option('--fields', default='text', help='提取字段，逗号分隔，如 text,href,id,class,loc',
              show_default=True)
@click.option('--limit', default=200, help='最多返回多少条', show_default=True)
@click.option('--filename', default=None, help='保存结果到 JSON 文件')
def cmd_query(session, selector, fields, limit, filename):
    """按选择器批量查询元素，提取指定属性。

    \b
    SELECTOR  元素定位器（DrissionPage 完整语法）

    \b
    --fields 支持的字段:
      text   元素文本内容
      tag    标签名
      loc    推荐定位器（方便后续操作）
      href   链接地址
      src    图片/资源地址
      id     id 属性
      class  class 属性
      其他   任意 HTML 属性名

    \b
    示例:
      dp query "css:.job-name" --fields "text,loc"
      dp query "css:a[href]" --fields "text,href"
      dp query "css:.salary" --fields "text"
      dp query "xpath://h2" --fields "text,id,class"
      dp query "css:.job-name" --fields "text" --filename jobs.json
    """
    page = _get_page(session)
    field_list = [f.strip() for f in fields.split(',') if f.strip()]
    try:
        results = query_elements(page, selector, field_list, limit=limit)
        data = {'count': len(results), 'selector': selector, 'records': results}
        if filename:
            Path(filename).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'
            )
            ok(data, msg=f'查询到 {len(results)} 个元素，保存到 {filename}')
        else:
            ok(data, msg=f'查询到 {len(results)} 个元素')
    except Exception as e:
        error(f'查询失败', code='QUERY_FAILED', detail=str(e))


# ═══════════════════════════════════════════════
# 元素交互
# ═══════════════════════════════════════════════

@cli.command('click')
@click.argument('locator')
@session_option
@click.option('--index', default=1, help='第几个匹配元素', show_default=True)
@click.option('--by-js', is_flag=True, help='使用 JavaScript 点击')
@click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
def cmd_click(locator, session, index, by_js, timeout):
    """点击元素。支持所有 DrissionPage 定位语法。

    \b
    定位语法示例:
      text:登录          按文本查找
      text=精确文本      精确文本匹配
      @id=btn-submit     按属性查找
      css:.btn-primary   CSS 选择器
      xpath://button     XPath
      #my-id             ID 快捷方式
      .my-class          class 快捷方式
      @@tag()=button@@text():提交  多条件与
    
    \b
    示例:
      dp click "text:登录"
      dp click "#submit-btn"
      dp click "css:.btn-primary" --by-js
    """
    page = _get_page(session)
    try:
        ele = page.ele(locator, index=index, timeout=timeout)
        if not ele or ele.__class__.__name__ == 'NoneElement':
            error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
            return
        if by_js:
            ele.click(by_js=True)
        else:
            ele.click()
        ok({'locator': locator, 'tag': ele.tag,
            'text': (ele.raw_text or '').strip()[:100]},
           msg='点击成功')
    except Exception as e:
        error(f'点击失败: {locator}', code='CLICK_FAILED', detail=str(e))


@cli.command()
@click.argument('locator')
@session_option
@click.option('--index', default=1, help='第几个匹配元素', show_default=True)
@click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
def dblclick(locator, session, index, timeout):
    """双击元素。"""
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


@cli.command()
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
      dp fill "#password" "my password"
      dp fill "css:textarea" "多行\\n文本"
    """
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
@click.option('--by-index', 'sel_by_index', default=None, type=int,
              help='按位置索引选择（从1开始）')
@click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
def cmd_select(locator, value, session, index, by_text, sel_by_index, timeout):
    """选择下拉框选项。

    \b
    示例:
      dp select "@name=city" beijing
      dp select "css:select#role" admin --by-text
      dp select "#size" "" --by-index 2
    """
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


@cli.command()
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


@cli.command()
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


@cli.command()
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


# ═══════════════════════════════════════════════
# 键盘操作
# ═══════════════════════════════════════════════

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
            # 组合键处理
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


# ═══════════════════════════════════════════════
# 滚动操作
# ═══════════════════════════════════════════════

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
      dp scroll --y -200       (向上滚动)
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
      dp scroll-to "text:更多内容"
    """
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


# ═══════════════════════════════════════════════
# 对话框处理
# ═══════════════════════════════════════════════

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


# ═══════════════════════════════════════════════
# 标签页管理
# ═══════════════════════════════════════════════

@cli.command('tab-list')
@session_option
def tab_list(session):
    """列出所有标签页。"""
    page = _get_page(session)
    try:
        tabs = []
        for i, tab_id in enumerate(page.browser.tab_ids):
            tab = page.browser.get_tab(tab_id)
            tabs.append({
                'index': i,
                'id': tab_id,
                'url': tab.url,
                'title': tab.title,
                'active': tab_id == page.tab_id,
            })
        ok({'tabs': tabs, 'count': len(tabs)})
    except Exception as e:
        error(f'获取标签页列表失败', code='TAB_FAILED', detail=str(e))


@cli.command('tab-new')
@click.argument('url', required=False)
@session_option
@click.option('--background', is_flag=True, help='在后台打开')
def tab_new(url, session, background):
    """新建标签页。

    \b
    示例:
      dp tab-new
      dp tab-new https://example.com
      dp tab-new https://example.com --background
    """
    page = _get_page(session)
    try:
        new_tab = page.browser.new_tab(url=url or '', background=background)
        ok({'id': new_tab.tab_id, 'url': new_tab.url,
            'title': new_tab.title}, msg='新标签页已创建')
    except Exception as e:
        error(f'创建标签页失败', code='TAB_FAILED', detail=str(e))


@cli.command('tab-select')
@click.argument('index_or_id')
@session_option
def tab_select(index_or_id, session):
    """切换到指定标签页（序号从0开始，或传入 tab_id）。

    \b
    示例:
      dp tab-select 0
      dp tab-select 2
    """
    page = _get_page(session)
    try:
        try:
            idx = int(index_or_id)
            tab_ids = page.browser.tab_ids
            if idx < 0 or idx >= len(tab_ids):
                error(f'标签页序号越界: {idx}', code='TAB_NOT_FOUND')
                return
            tab_id = tab_ids[idx]
        except ValueError:
            tab_id = index_or_id

        tab = page.browser.get_tab(tab_id)
        tab.set.activate()
        # 更新会话的当前 tab 信息
        sess = load_session(session)
        sess['active_tab'] = tab_id
        save_session(session, sess)
        ok({'id': tab_id, 'url': tab.url, 'title': tab.title}, msg='标签页已切换')
    except Exception as e:
        error(f'切换标签页失败', code='TAB_FAILED', detail=str(e))


@cli.command('tab-close')
@click.argument('index_or_id', required=False)
@session_option
def tab_close(index_or_id, session):
    """关闭标签页（默认关闭当前页）。"""
    page = _get_page(session)
    try:
        if index_or_id is None:
            page.close()
            ok(msg='当前标签页已关闭')
        else:
            try:
                idx = int(index_or_id)
                tab_ids = page.browser.tab_ids
                tab_id = tab_ids[idx]
            except ValueError:
                tab_id = index_or_id
            tab = page.browser.get_tab(tab_id)
            tab.close()
            ok({'id': tab_id}, msg='标签页已关闭')
    except Exception as e:
        error(f'关闭标签页失败', code='TAB_FAILED', detail=str(e))


# ═══════════════════════════════════════════════
# 截图与媒体
# ═══════════════════════════════════════════════

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
        page.run_cdp('Page.printToPDF', transferMode='ReturnAsBase64')
        result = page.run_cdp('Page.printToPDF', transferMode='ReturnAsBase64')
        import base64
        Path(filename).write_bytes(base64.b64decode(result['data']))
        ok({'filename': str(Path(filename).absolute())}, msg='PDF 已保存')
    except Exception as e:
        error(f'保存 PDF 失败', code='PDF_FAILED', detail=str(e))


# ═══════════════════════════════════════════════
# JavaScript 执行
# ═══════════════════════════════════════════════

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
            # 检测是否为函数形式
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


# ═══════════════════════════════════════════════
# Cookie 管理
# ═══════════════════════════════════════════════

@cli.command('cookie-list')
@session_option
@click.option('--domain', default=None, help='按域名过滤')
@click.option('--url', default=None, help='按 URL 过滤')
def cookie_list(session, domain, url):
    """列出所有 Cookie。

    \b
    示例:
      dp cookie-list
      dp cookie-list --domain example.com
    """
    page = _get_page(session)
    try:
        cookies = page.cookies(all_domains=True).as_dict() if not domain else page.cookies().as_dict()
        cookie_list_data = []
        for name, value in cookies.items():
            cookie_list_data.append({'name': name, 'value': value})
        ok({'cookies': cookie_list_data, 'count': len(cookie_list_data)})
    except Exception as e:
        error(f'获取 Cookie 失败', code='COOKIE_FAILED', detail=str(e))


@cli.command('cookie-get')
@click.argument('name')
@session_option
def cookie_get(name, session):
    """获取指定名称的 Cookie 值。"""
    page = _get_page(session)
    try:
        cookies = page.cookies().as_dict()
        value = cookies.get(name)
        if value is None:
            error(f'Cookie 不存在: {name}', code='COOKIE_NOT_FOUND')
            return
        ok({'name': name, 'value': value})
    except Exception as e:
        error(f'获取 Cookie 失败', code='COOKIE_FAILED', detail=str(e))


@cli.command('cookie-set')
@click.argument('name')
@click.argument('value')
@session_option
@click.option('--domain', default=None, help='Cookie 域名')
@click.option('--path', default='/', help='Cookie 路径', show_default=True)
@click.option('--http-only', is_flag=True, help='设置 HttpOnly')
@click.option('--secure', is_flag=True, help='设置 Secure')
def cookie_set(name, value, session, domain, path, http_only, secure):
    """设置 Cookie。

    \b
    示例:
      dp cookie-set session_id abc123
      dp cookie-set token xxx --domain example.com --http-only --secure
    """
    page = _get_page(session)
    try:
        kwargs = {'name': name, 'value': value, 'path': path}
        if domain:
            kwargs['domain'] = domain
        if http_only:
            kwargs['httpOnly'] = True
        if secure:
            kwargs['secure'] = True
        page.set.cookies(kwargs)
        ok({'name': name, 'value': value}, msg='Cookie 已设置')
    except Exception as e:
        error(f'设置 Cookie 失败', code='COOKIE_FAILED', detail=str(e))


@cli.command('cookie-delete')
@click.argument('name')
@session_option
def cookie_delete(name, session):
    """删除指定 Cookie。"""
    page = _get_page(session)
    try:
        page.run_cdp('Network.deleteCookies', name=name)
        ok({'name': name}, msg='Cookie 已删除')
    except Exception as e:
        error(f'删除 Cookie 失败', code='COOKIE_FAILED', detail=str(e))


@cli.command('cookie-clear')
@session_option
def cookie_clear(session):
    """清除所有 Cookie。"""
    page = _get_page(session)
    try:
        page.run_cdp('Network.clearBrowserCookies')
        ok(msg='所有 Cookie 已清除')
    except Exception as e:
        error(f'清除 Cookie 失败', code='COOKIE_FAILED', detail=str(e))


# ═══════════════════════════════════════════════
# LocalStorage / SessionStorage
# ═══════════════════════════════════════════════

@cli.command('localstorage-list')
@session_option
def localstorage_list(session):
    """列出所有 localStorage 条目。"""
    page = _get_page(session)
    try:
        result = page.run_js('return JSON.stringify(Object.fromEntries(Object.entries(localStorage)))',
                             as_expr=True)
        data = json.loads(result) if isinstance(result, str) else result or {}
        ok({'storage': data, 'count': len(data)})
    except Exception as e:
        error(f'获取 localStorage 失败', code='STORAGE_FAILED', detail=str(e))


@cli.command('localstorage-get')
@click.argument('key')
@session_option
def localstorage_get(key, session):
    """获取 localStorage 指定键的值。"""
    page = _get_page(session)
    try:
        value = page.local_storage(key)
        ok({'key': key, 'value': value})
    except Exception as e:
        error(f'获取 localStorage 失败', code='STORAGE_FAILED', detail=str(e))


@cli.command('localstorage-set')
@click.argument('key')
@click.argument('value')
@session_option
def localstorage_set(key, value, session):
    """设置 localStorage 键值。"""
    page = _get_page(session)
    try:
        page.run_js(f'localStorage.setItem({json.dumps(key)}, {json.dumps(value)})', as_expr=True)
        ok({'key': key, 'value': value}, msg='localStorage 已设置')
    except Exception as e:
        error(f'设置 localStorage 失败', code='STORAGE_FAILED', detail=str(e))


@cli.command('localstorage-delete')
@click.argument('key')
@session_option
def localstorage_delete(key, session):
    """删除 localStorage 指定键。"""
    page = _get_page(session)
    try:
        page.run_js(f'localStorage.removeItem({json.dumps(key)})', as_expr=True)
        ok({'key': key}, msg='localStorage 键已删除')
    except Exception as e:
        error(f'删除 localStorage 失败', code='STORAGE_FAILED', detail=str(e))


@cli.command('localstorage-clear')
@session_option
def localstorage_clear(session):
    """清除所有 localStorage。"""
    page = _get_page(session)
    try:
        page.run_js('localStorage.clear()', as_expr=True)
        ok(msg='localStorage 已清除')
    except Exception as e:
        error(f'清除 localStorage 失败', code='STORAGE_FAILED', detail=str(e))


@cli.command('sessionstorage-list')
@session_option
def sessionstorage_list(session):
    """列出所有 sessionStorage 条目。"""
    page = _get_page(session)
    try:
        result = page.run_js('return JSON.stringify(Object.fromEntries(Object.entries(sessionStorage)))',
                             as_expr=True)
        data = json.loads(result) if isinstance(result, str) else result or {}
        ok({'storage': data, 'count': len(data)})
    except Exception as e:
        error(f'获取 sessionStorage 失败', code='STORAGE_FAILED', detail=str(e))


@cli.command('sessionstorage-get')
@click.argument('key')
@session_option
def sessionstorage_get(key, session):
    """获取 sessionStorage 指定键的值。"""
    page = _get_page(session)
    try:
        value = page.session_storage(key)
        ok({'key': key, 'value': value})
    except Exception as e:
        error(f'获取 sessionStorage 失败', code='STORAGE_FAILED', detail=str(e))


@cli.command('sessionstorage-set')
@click.argument('key')
@click.argument('value')
@session_option
def sessionstorage_set(key, value, session):
    """设置 sessionStorage 键值。"""
    page = _get_page(session)
    try:
        page.run_js(f'sessionStorage.setItem({json.dumps(key)}, {json.dumps(value)})', as_expr=True)
        ok({'key': key, 'value': value}, msg='sessionStorage 已设置')
    except Exception as e:
        error(f'设置 sessionStorage 失败', code='STORAGE_FAILED', detail=str(e))


@cli.command('sessionstorage-clear')
@session_option
def sessionstorage_clear(session):
    """清除所有 sessionStorage。"""
    page = _get_page(session)
    try:
        page.run_js('sessionStorage.clear()', as_expr=True)
        ok(msg='sessionStorage 已清除')
    except Exception as e:
        error(f'清除 sessionStorage 失败', code='STORAGE_FAILED', detail=str(e))


# ═══════════════════════════════════════════════
# 元素信息查询（DrissionPage 独有优势）
# ═══════════════════════════════════════════════

@cli.command()
@click.argument('locator')
@session_option
@click.option('--index', default=1, help='第几个匹配元素', show_default=True)
@click.option('--include-rect', is_flag=True, help='包含位置和尺寸信息')
@click.option('--include-style', is_flag=True, help='包含计算样式')
@click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
def inspect(locator, session, index, include_rect, include_style, timeout):
    """查询元素详细信息（DrissionPage 独有：位置/尺寸/样式/状态/属性）。

    \b
    示例:
      dp inspect "#submit-btn"
      dp inspect "text:登录" --include-rect
      dp inspect "css:input[name=email]" --include-style
    """
    page = _get_page(session)
    try:
        ele = page.ele(locator, index=index, timeout=timeout)
        if not ele or ele.__class__.__name__ == 'NoneElement':
            error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
            return

        info = {
            'tag': ele.tag,
            'html': ele.html[:500],
            'text': (ele.raw_text or '').strip()[:300],
            'attrs': ele.attrs,
            'states': {
                'is_displayed': ele.states.is_displayed,
                'is_enabled': ele.states.is_enabled,
                'is_clickable': ele.states.is_clickable,
                'is_in_viewport': ele.states.is_in_viewport,
            },
        }

        if include_rect:
            try:
                info['rect'] = {
                    'location': list(ele.rect.location),
                    'size': list(ele.rect.size),
                    'midpoint': list(ele.rect.midpoint),
                }
            except Exception:
                pass

        if include_style:
            styles = {}
            for prop in ('display', 'visibility', 'opacity', 'color',
                         'background-color', 'font-size', 'z-index', 'position'):
                try:
                    styles[prop] = ele.style(prop)
                except Exception:
                    pass
            info['styles'] = styles

        ok(info)
    except Exception as e:
        error(f'查询元素失败: {locator}', code='INSPECT_FAILED', detail=str(e))


@cli.command('find')
@click.argument('locator')
@session_option
@click.option('--all', 'find_all', is_flag=True, help='返回所有匹配元素')
@click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
def find(locator, session, find_all, timeout):
    """查找元素并返回信息。

    \b
    示例:
      dp find "css:a"              查找第一个链接
      dp find "css:a" --all        查找所有链接
      dp find "text:登录"
    """
    page = _get_page(session)
    try:
        if find_all:
            eles = page.eles(locator, timeout=timeout)
            results = []
            for i, ele in enumerate(eles):
                results.append({
                    'index': i,
                    'tag': ele.tag,
                    'text': (ele.raw_text or '').strip()[:100],
                    'attrs': {k: v for k, v in ele.attrs.items()
                              if k in ('id', 'class', 'href', 'name', 'type', 'value')},
                })
            ok({'count': len(results), 'elements': results})
        else:
            ele = page.ele(locator, timeout=timeout)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                ok({'found': False, 'locator': locator})
                return
            ok({'found': True,
                'tag': ele.tag,
                'text': (ele.raw_text or '').strip()[:100],
                'attrs': ele.attrs})
    except Exception as e:
        error(f'查找失败: {locator}', code='FIND_FAILED', detail=str(e))


# ═══════════════════════════════════════════════
# 等待
# ═══════════════════════════════════════════════

@cli.command()
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
            ok(format_page_info(page), msg=f'URL 已变化')
        elif locator:
            ele = page.wait.ele_displayed(locator, timeout=timeout)
            ok({'locator': locator, 'found': bool(ele)}, msg='元素已出现')
        elif locator_gone:
            page.wait.ele_hidden(locator_gone, timeout=timeout)
            ok({'locator': locator_gone}, msg='元素已消失')
        elif text:
            from time import perf_counter, sleep as _sleep
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


# ═══════════════════════════════════════════════
# 网络监听（DrissionPage 独有能力）
# ═══════════════════════════════════════════════

@cli.command()
@session_option
@click.option('--filter', 'url_filter', default=None,
              help='URL 过滤关键字，如 "api/user"')
@click.option('--count', default=10, help='最多捕获请求数', show_default=True)
@click.option('--timeout', default=30, help='监听超时秒数', show_default=True)
@click.option('--method', default=None, help='过滤请求方法，如 POST')
def listen(session, url_filter, count, timeout, method):
    """监听网络请求（抓包）。在执行 goto/click 前先 listen，然后执行操作，最后读取结果。

    \b
    这是 DrissionPage 的核心独特能力，可以精确捕获 XHR/Fetch/图片等任意请求。

    \b
    示例:
      dp listen --filter "api/login"
      dp listen --count 5 --timeout 10
    """
    page = _get_page(session)
    try:
        targets = url_filter if url_filter else None
        page.listen.start(targets=targets, method=method)
        ok(msg=f'已开始监听，过滤: {url_filter or "全部"}')
    except Exception as e:
        error(f'启动监听失败', code='LISTEN_FAILED', detail=str(e))


@cli.command('listen-stop')
@session_option
@click.option('--count', default=1, help='等待数据包数量', show_default=True)
@click.option('--timeout', default=10, help='等待数据超时秒数', show_default=True)
def listen_stop(session, count, timeout):
    """停止监听并获取捕获的网络请求数据。"""
    page = _get_page(session)
    try:
        packets = page.listen.wait(count=count, timeout=timeout, fit_count=False)
        results = []
        if packets:
            pkts = packets if isinstance(packets, list) else [packets]
            for pkt in pkts:
                try:
                    item = {
                        'url': pkt.url,
                        'method': pkt.method,
                        'status': pkt.response.status if pkt.response else None,
                        'type': pkt.resourceType,
                    }
                    try:
                        item['response_body'] = pkt.response.body
                    except Exception:
                        pass
                    results.append(item)
                except Exception:
                    continue
        page.listen.stop()
        ok({'packets': results, 'count': len(results)})
    except Exception as e:
        error(f'获取监听数据失败', code='LISTEN_FAILED', detail=str(e))


# ═══════════════════════════════════════════════
# Session 模式（纯 HTTP，不启动浏览器）
# ═══════════════════════════════════════════════

@cli.command('http-get')
@click.argument('url')
@click.option('--headers', default=None, help='JSON 格式的 Headers')
@click.option('--proxy', default=None, help='代理地址')
@click.option('--timeout', default=30, help='超时秒数', show_default=True)
@click.option('--output', default=None, help='响应体保存路径')
def http_get(url, headers, proxy, timeout, output):
    """发送 HTTP GET 请求（不启动浏览器，高效爬虫模式）。

    \b
    示例:
      dp http-get https://api.example.com/users
      dp http-get https://example.com --output page.html
    """
    try:
        from DrissionPage import SessionPage
        from DrissionPage._configs.session_options import SessionOptions

        so = SessionOptions()
        if proxy:
            so.set_proxies(http=proxy, https=proxy)
        if headers:
            so.set_headers(json.loads(headers))

        page = SessionPage(session_or_options=so)
        page.get(url, timeout=timeout)

        result = {
            'url': page.url,
            'status_code': page.response.status_code if page.response else None,
            'content_type': page.response.headers.get('content-type', '') if page.response else '',
        }

        if output:
            Path(output).write_bytes(page.response.content)
            result['saved_to'] = str(Path(output).absolute())
        else:
            try:
                result['body'] = page.response.text[:5000]
            except Exception:
                result['body'] = '<binary>'

        ok(result)
    except Exception as e:
        error(f'HTTP GET 失败', code='HTTP_FAILED', detail=str(e))


@cli.command('http-post')
@click.argument('url')
@click.option('--data', default=None, help='POST 数据（JSON 格式）')
@click.option('--form', default=None, help='表单数据（JSON 格式）')
@click.option('--headers', default=None, help='JSON 格式的 Headers')
@click.option('--proxy', default=None, help='代理地址')
@click.option('--timeout', default=30, help='超时秒数', show_default=True)
def http_post(url, data, form, headers, proxy, timeout):
    """发送 HTTP POST 请求（纯 HTTP 模式）。

    \b
    示例:
      dp http-post https://api.example.com/login --data '{"user":"admin","pass":"123"}'
      dp http-post https://example.com/form --form '{"name":"test"}'
    """
    try:
        from DrissionPage import SessionPage
        from DrissionPage._configs.session_options import SessionOptions

        so = SessionOptions()
        if proxy:
            so.set_proxies(http=proxy, https=proxy)
        if headers:
            so.set_headers(json.loads(headers))

        page = SessionPage(session_or_options=so)

        kwargs = {'timeout': timeout}
        if data:
            kwargs['json'] = json.loads(data)
        elif form:
            kwargs['data'] = json.loads(form)

        page.post(url, **kwargs)

        result = {
            'url': page.url,
            'status_code': page.response.status_code if page.response else None,
        }
        try:
            result['body'] = page.response.json()
        except Exception:
            try:
                result['body'] = page.response.text[:3000]
            except Exception:
                result['body'] = '<binary>'

        ok(result)
    except Exception as e:
        error(f'HTTP POST 失败', code='HTTP_FAILED', detail=str(e))


# ═══════════════════════════════════════════════
# 窗口/视口控制
# ═══════════════════════════════════════════════

@cli.command()
@click.argument('width', type=int)
@click.argument('height', type=int)
@session_option
def resize(width, height, session):
    """调整浏览器窗口大小。

    \b
    示例:
      dp resize 1920 1080
      dp resize 375 812
    """
    page = _get_page(session)
    try:
        page.set.window.size(width, height)
        ok({'width': width, 'height': height}, msg='窗口大小已调整')
    except Exception as e:
        error(f'调整窗口大小失败', code='RESIZE_FAILED', detail=str(e))


@cli.command()
@session_option
def maximize(session):
    """最大化浏览器窗口。"""
    page = _get_page(session)
    try:
        page.set.window.max()
        ok(msg='窗口已最大化')
    except Exception as e:
        error(f'最大化失败', code='WINDOW_FAILED', detail=str(e))


# ═══════════════════════════════════════════════
# 状态保存/加载
# ═══════════════════════════════════════════════

@cli.command('state-save')
@click.argument('filename', default='state.json')
@session_option
def state_save(filename, session):
    """保存浏览器状态（Cookie + localStorage）到文件。

    \b
    示例:
      dp state-save
      dp state-save auth.json
    """
    page = _get_page(session)
    try:
        state = {
            'cookies': page.cookies(all_domains=True).as_dict(),
            'url': page.url,
        }
        try:
            ls = page.run_js(
                'return JSON.stringify(Object.fromEntries(Object.entries(localStorage)))',
                as_expr=True)
            state['localStorage'] = json.loads(ls) if isinstance(ls, str) else ls or {}
        except Exception:
            state['localStorage'] = {}

        Path(filename).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        ok({'filename': str(Path(filename).absolute()),
            'cookies_count': len(state['cookies'])}, msg='状态已保存')
    except Exception as e:
        error(f'保存状态失败', code='STATE_FAILED', detail=str(e))


@cli.command('state-load')
@click.argument('filename', default='state.json')
@session_option
def state_load(filename, session):
    """从文件加载浏览器状态（Cookie + localStorage）。

    \b
    示例:
      dp state-load
      dp state-load auth.json
    """
    page = _get_page(session)
    try:
        state = json.loads(Path(filename).read_text(encoding='utf-8'))

        # 恢复 Cookie
        if 'cookies' in state:
            for name, value in state['cookies'].items():
                try:
                    page.set.cookies({'name': name, 'value': value})
                except Exception:
                    pass

        # 恢复 localStorage
        if 'localStorage' in state and state['localStorage']:
            for k, v in state['localStorage'].items():
                try:
                    page.run_js(f'localStorage.setItem({json.dumps(k)}, {json.dumps(v)})', as_expr=True)
                except Exception:
                    pass

        ok({'filename': filename,
            'cookies_restored': len(state.get('cookies', {}))}, msg='状态已加载')
    except FileNotFoundError:
        error(f'状态文件不存在: {filename}', code='FILE_NOT_FOUND')
    except Exception as e:
        error(f'加载状态失败', code='STATE_FAILED', detail=str(e))


# ═══════════════════════════════════════════════
# 配置管理（原有 dp 功能增强）
# ═══════════════════════════════════════════════

@cli.command('config-set')
@click.option('-p', '--browser-path', default=None, help='设置浏览器路径')
@click.option('-u', '--user-path', default=None, help='设置用户数据路径')
@click.option('-c', '--copy-config', is_flag=True, help='复制默认配置文件到当前目录')
def config_set(browser_path, user_path, copy_config):
    """修改 DrissionPage 配置文件。

    \b
    示例:
      dp config-set --browser-path /usr/bin/google-chrome
      dp config-set --user-path /home/user/.chrome-data
      dp config-set --copy-config
    """
    from DrissionPage._configs.chromium_options import ChromiumOptions
    from DrissionPage._functions.tools import configs_to_here

    if copy_config:
        configs_to_here()
        ok(msg='配置文件已复制到当前目录')

    if browser_path or user_path:
        co = ChromiumOptions()
        if browser_path:
            co.set_browser_path(browser_path)
        if user_path:
            co.set_user_data_path(user_path)
        co.save()
        ok(msg='配置已保存')


def main():
    cli()


if __name__ == '__main__':
    main()
