# -*- coding:utf-8 -*-
"""快照与数据提取命令: snapshot / extract / query / find / inspect / dom"""
import json
from pathlib import Path

import click

from dp_cli.output import ok, error
from dp_cli.session import save_refs
from dp_cli.snapshot import (extract_structured, query_elements,
                              take_a11y_snapshot, render_a11y_text,
                              render_a11y_plain_text)
from dp_cli.snapshot.utils import suggest_locator
from dp_cli.commands._utils import session_option, _get_page, records_to_csv, resolve_locator


def register(cli):

    @cli.command()
    @session_option
    @click.option('--mode',
                  type=click.Choice(['full', 'brief', 'text']),
                  default='full', show_default=True, help='快照模式')
    @click.option('--selector', default=None, help='限定快照范围的 CSS 选择器')
    @click.option('--format', 'fmt', type=click.Choice(['json', 'text']),
                  default='text', show_default=True, help='输出格式')
    @click.option('--filename', default=None, help='保存到文件路径')
    def snapshot(session, mode, selector, fmt, filename):
        """获取页面快照（基于浏览器原生 a11y tree，通用性极强）。

        \b
        模式说明（默认 full）:
          full   【默认】完整页面快照，包含所有内容和交互元素
          brief  精简模式，保留结构+交互，截断长文本（省 token）
          text   纯文本模式，按阅读顺序输出可见文本

        \b
        示例:
          dp snapshot                          # 完整快照（推荐首次调用）
          dp snapshot --mode brief             # 精简模式（省 token，适合循环调用）
          dp snapshot --mode text              # 纯文本（全量文字内容）
          dp snapshot --selector ".main"        # 只获取指定区域
          dp snapshot --format json            # JSON 格式输出
        """
        page = _get_page(session)

        try:
            data = take_a11y_snapshot(page, selector=selector)
        except Exception as e:
            error('获取页面快照失败', code='SNAPSHOT_FAILED', detail=str(e))
            return

        # 收集 ref 映射（所有模式都收集，便于后续 ref:N 引用）
        refs = {}
        if fmt == 'json':
            render_a11y_text(data, refs=refs)  # 触发编号分配
            output = json.dumps({'status': 'ok', 'data': data},
                                ensure_ascii=False, indent=2)
        elif mode == 'text':
            output = render_a11y_plain_text(data, refs=refs)
        elif mode == 'brief':
            output = render_a11y_text(data, brief=True, refs=refs)
        else:
            output = render_a11y_text(data, refs=refs)

        # 保存 refs 映射到 session（供 ref:N 解析使用）
        if refs:
            url = data.get('page', {}).get('url', '')
            save_refs(session, url, refs)

        if filename:
            Path(filename).write_text(output, encoding='utf-8')
            ok(msg=f'快照已保存到 {filename}')
        else:
            click.echo(output)

    @cli.command('extract')
    @session_option
    @click.argument('container')
    @click.argument('fields_json')
    @click.option('--limit', default=None, help='最多提取多少条记录', show_default=True)
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
          dp extract "ref:30" '{"title":"css:h3"}'  # 用快照编号定位容器

        \b
        先用 snapshot 了解页面结构，再用 extract 定位容器和字段。
        """
        container = resolve_locator(container, session)
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
    @click.option('--limit', default=None, help='最多返回多少条', show_default=True)
    @click.option('--filename', default=None, help='保存结果到 JSON 文件')
    def cmd_query(session, selector, fields, limit, filename):
        """按选择器查询元素，提取内容和定位器。支持动态渲染内容。

        \b
        --fields 支持的字段（默认 text,loc）:
          text        元素可见文本（raw_text，过滤隐藏反爬文本）
          tag         标签名
          loc         推荐定位器（简短，可直接用于 click/fill 等）
          css         精确 CSS 路径（JS 生成，可唯一定位）
          xpath       精确 XPath（JS 生成）
          html        innerHTML
          outer_html  完整 outerHTML
          href/src/id/class  常用属性
          其他        任意 HTML 属性名

        \b
        用法示例:
          dp query "css:.job-name"                           # 默认返回文本+定位器
          dp query "ref:57"                                  # 用快照编号查询
          dp query "ref:57" --fields "text,css,tag,class"   # 获取精确 CSS 路径
          dp query "css:a[href]" --fields "text,href"
          dp query "css:.desc" --fields "text,html"         # 获取 innerHTML
        """
        selector = resolve_locator(selector, session)
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
          dp find "ref:5"
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        try:
            if find_all:
                eles = page.eles(locator, timeout=timeout)
                results = []
                for i, ele in enumerate(eles):
                    results.append({
                        'index': i,
                        'tag': ele.tag,
                        'text': (ele.raw_text or '').strip(),
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
                    'text': (ele.raw_text or '').strip(),
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
          dp inspect "ref:5" --include-rect
          dp inspect "css:input" --include-style
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        try:
            ele = page.ele(locator, index=index, timeout=timeout)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                return
            info = {
                'tag': ele.tag,
                'text': (ele.raw_text or '').strip(),
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
                    'location': list(rect.location),
                    'size': list(rect.size),
                    'midpoint': list(rect.midpoint),
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

    # ---- DOM 遍历命令 ----

    def _ele_summary(ele, max_text=60):
        """生成元素的简洁摘要"""
        if not ele or ele.__class__.__name__ == 'NoneElement':
            return None
        tag = ele.tag
        attrs = ele.attrs or {}
        cls = attrs.get('class', '')
        eid = attrs.get('id', '')
        text = (ele.raw_text or '').strip()
        label = tag
        if eid:
            label += f'#{eid}'
        elif cls:
            first_cls = cls.strip().split()[0] if cls.strip() else ''
            if first_cls:
                label += f'.{first_cls}'
        loc = suggest_locator(tag, attrs, text[:50])
        summary = {'tag': label, 'loc': loc}
        if text:
            summary['text'] = text[:max_text] + ('…' if len(text) > max_text else '')
        return summary

    @cli.command('dom')
    @click.argument('locator')
    @session_option
    @click.option('--direction', '-d',
                  type=click.Choice(['parent', 'children', 'siblings', 'all']),
                  default='all', show_default=True,
                  help='查询方向')
    @click.option('--depth', default=1, show_default=True,
                  help='向上查几层父节点（仅 parent/all 生效）')
    @click.option('--index', default=1, help='第几个匹配元素', show_default=True)
    @click.option('--timeout', default=10, help='等待超时秒数', show_default=True)
    def cmd_dom(locator, session, direction, depth, index, timeout):
        """查询元素的 DOM 上下文（父/子/兄弟节点）。

        \b
        精确定位元素时，先用 snapshot 找到目标，再用 dom 查看周围结构。

        \b
        示例:
          dp dom "ref:21"                         # 查看父/子/兄弟全部
          dp dom "ref:21" -d parent               # 只看父节点链
          dp dom "ref:21" -d parent --depth 3     # 向上追溯 3 层
          dp dom "ref:21" -d children             # 只看子节点
          dp dom "ref:21" -d siblings             # 只看兄弟节点
          dp dom "css:.job-name" -d all           # 用 CSS 选择器
        """
        locator = resolve_locator(locator, session)
        page = _get_page(session)
        try:
            ele = page.ele(locator, index=index, timeout=timeout)
            if not ele or ele.__class__.__name__ == 'NoneElement':
                error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                return

            result = {'self': _ele_summary(ele)}

            # ---- parent chain ----
            if direction in ('parent', 'all'):
                parents = []
                cur = ele
                for _ in range(depth):
                    try:
                        par = cur.parent()
                        if not par or par.__class__.__name__ == 'NoneElement':
                            break
                        if par.tag in ('html', 'body'):
                            parents.append({'tag': par.tag})
                            break
                        parents.append(_ele_summary(par))
                        cur = par
                    except Exception:
                        break
                result['parents'] = parents

            # ---- children ----
            if direction in ('children', 'all'):
                children = []
                try:
                    for child in ele.children():
                        s = _ele_summary(child)
                        if s:
                            children.append(s)
                except Exception:
                    pass
                result['children'] = children

            # ---- siblings (prev + next) ----
            if direction in ('siblings', 'all'):
                prev_sibs = []
                next_sibs = []
                try:
                    for sib in ele.prevs():
                        s = _ele_summary(sib)
                        if s:
                            prev_sibs.append(s)
                    prev_sibs.reverse()
                except Exception:
                    pass
                try:
                    for sib in ele.nexts():
                        s = _ele_summary(sib)
                        if s:
                            next_sibs.append(s)
                except Exception:
                    pass
                result['prev_siblings'] = prev_sibs
                result['next_siblings'] = next_sibs

            ok(result)
        except Exception as e:
            error(f'DOM 查询失败: {locator}', code='DOM_FAILED', detail=str(e))
