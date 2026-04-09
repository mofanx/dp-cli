# -*- coding:utf-8 -*-
"""快照与数据提取命令: snapshot / extract / query / find / inspect"""
import json
from pathlib import Path

import click

from dp_cli.output import ok, error
from dp_cli.snapshot import (take_snapshot, render_snapshot_text,
                              extract_structured, query_elements)
from dp_cli.commands._utils import session_option, _get_page, records_to_csv


def register(cli):

    @cli.command()
    @session_option
    @click.option('--mode', type=click.Choice(['auto', 'interactive', 'content', 'full', 'text']),
                  default='auto', show_default=True, help='快照模式')
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
        模式说明（默认 auto，无需手动选）:
          auto         自动检测页面类型并输出最有用信息（推荐）
                       · 列表页 → 输出前5张卡片 + extract 一键提取命令
                       · 表单页 → 输出所有可交互元素及定位器
                       · 内容页 → 输出去噪语义内容树
          interactive  强制只列出可交互元素（登录/操控场景）
          content      强制输出语义内容树（含区块分隔标记）
          full         完整 DOM 树（调试用）
          text         页面纯文本

        \b
        示例:
          dp snapshot                          # 自动模式（推荐）
          dp snapshot --mode interactive       # 找按钮/输入框
          dp snapshot --mode content           # 看页面文字内容
          dp snapshot --selector "css:.main"   # 限定区域
          dp snapshot --format json            # JSON 输出给程序处理
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
        CONTAINER    容器元素的定位器（每个容器对应一条记录）
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
          dp extract "css:.card" '{"title":"css:.title","url":{"selector":"css:a","attr":"href"}}'

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
                content = records_to_csv(results)
                if filename:
                    Path(filename).write_text(content, encoding='utf-8-sig')
                    ok(data, msg=f'已提取 {len(results)} 条记录，保存到 {filename}')
                else:
                    click.echo(content)
            elif filename:
                Path(filename).write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                ok(data, msg=f'已提取 {len(results)} 条记录，保存到 {filename}')
            else:
                ok(data, msg=f'已提取 {len(results)} 条记录')
        except Exception as e:
            error(f'提取失败', code='EXTRACT_FAILED', detail=str(e))

    @cli.command('query')
    @session_option
    @click.argument('selector')
    @click.option('--fields', default='text,loc', show_default=True,
                  help='提取字段，逗号分隔')
    @click.option('--limit', default=200, help='最多返回多少条', show_default=True)
    @click.option('--filename', default=None, help='保存结果到 JSON 文件')
    def cmd_query(session, selector, fields, limit, filename):
        """按选择器查询元素，提取内容和定位器。支持动态渲染内容。

        \b
        --fields 支持的字段（默认 text,loc）:
          text      元素文本内容
          tag       标签名
          loc       推荐 DrissionPage 定位器（简短，可直接用于 click/fill 等）
          css_path  精确 CSS 路径（JS 生成，可唯一定位，适合复杂场景）
          xpath     精确 XPath（JS 生成）
          href      链接地址
          src       图片/资源地址
          id        id 属性
          class     class 属性
          其他      任意 HTML 属性名

        \b
        用法示例:
          dp query "css:.job-name"                           # 默认返回文本+定位器
          dp query "text:部署和支持" --fields "text,loc,css_path,tag,class"
          dp query "css:a[href]" --fields "text,href"
          dp query "xpath://h2" --fields "text,id,class"
          dp query "css:.desc" --fields "text,css_path"     # 获取精确 CSS 路径反查
        """
        page = _get_page(session)
        field_list = [f.strip() for f in fields.split(',') if f.strip()]
        try:
            results = query_elements(page, selector, field_list, limit=limit)
            data = {'count': len(results), 'selector': selector, 'records': results}
            if filename:
                Path(filename).write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                ok(data, msg=f'查询到 {len(results)} 个元素，保存到 {filename}')
            else:
                ok(data, msg=f'查询到 {len(results)} 个元素')
        except Exception as e:
            error(f'查询失败', code='QUERY_FAILED', detail=str(e))

    @cli.command('find')
    @click.argument('locator')
    @session_option
    @click.option('--all', 'find_all', is_flag=True, help='返回所有匹配元素')
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def find(locator, session, find_all, timeout):
        """查找元素并返回信息。

        \b
        示例:
          dp find "css:a"
          dp find "css:a" --all
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

    @cli.command('inspect')
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
          dp inspect "css:input" --include-style
        """
        page = _get_page(session)
        try:
            ele = page.ele(locator, index=index, timeout=timeout)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                return
            info = {
                'tag': ele.tag,
                'text': (ele.raw_text or '').strip()[:200],
                'attrs': ele.attrs,
                'states': {
                    'is_displayed': ele.states.is_displayed,
                    'is_enabled': ele.states.is_enabled,
                    'is_checked': ele.states.is_checked if ele.tag in ('input', 'option') else None,
                    'is_clickable': ele.states.is_clickable,
                },
            }
            if include_rect:
                rect = ele.rect
                info['rect'] = {
                    'x': rect.x, 'y': rect.y,
                    'width': rect.width, 'height': rect.height,
                    'midpoint': rect.midpoint,
                }
            if include_style:
                styles = {}
                for prop in ('color', 'background-color', 'font-size',
                             'display', 'visibility', 'position', 'z-index'):
                    try:
                        styles[prop] = ele.style(prop)
                    except Exception:
                        pass
                info['styles'] = styles
            ok(info)
        except Exception as e:
            error(f'查询元素失败: {locator}', code='INSPECT_FAILED', detail=str(e))
