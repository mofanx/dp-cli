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
    @click.option('--mode', type=click.Choice(['interactive', 'content', 'full', 'text']),
                  default='interactive', show_default=True, help='快照模式')
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
          dp snapshot --mode content
          dp snapshot --mode content --selector "css:.main"
          dp snapshot --mode full
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
    @click.option('--fields', default='text', show_default=True,
                  help='提取字段，逗号分隔，如 text,href,id,class,loc')
    @click.option('--limit', default=200, help='最多返回多少条', show_default=True)
    @click.option('--filename', default=None, help='保存结果到 JSON 文件')
    def cmd_query(session, selector, fields, limit, filename):
        """按选择器批量查询元素，提取指定属性。

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
          dp query "css:.title" --fields "text,loc"
          dp query "css:a[href]" --fields "text,href"
          dp query "xpath://h2" --fields "text,id,class"
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
