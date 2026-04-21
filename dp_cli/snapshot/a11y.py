# -*- coding:utf-8 -*-
"""
a11y tree — 浏览器原生无障碍树获取与渲染

通过 Chrome DevTools Protocol Accessibility API 获取完整的 a11y tree，
为 AI 提供全面的页面结构理解。

核心流程：
  1. CDP getFullAXTree 获取扁平节点列表
  2. _normalize_node() 解包 AXValue 对象
  3. _build_tree() 构建嵌套树
  4. 为交互节点生成 DrissionPage 定位器
  5. render_a11y_text() 渲染为可读文本
  6. CDP 失败时降级到 JS fallback
"""
from .utils import suggest_locator

# 交互角色列表：需要生成定位器的角色
_INTERACTIVE_ROLES = frozenset({
    'button', 'link', 'textbox', 'combobox', 'checkbox', 'radio',
    'slider', 'spinbutton', 'tab', 'menuitem', 'searchbox', 'switch',
    'option', 'menuitemcheckbox', 'menuitemradio', 'treeitem',
})

# 语义角色：渲染时保留的非交互角色
_SEMANTIC_ROLES = frozenset({
    'heading', 'list', 'listitem', 'article', 'navigation', 'main',
    'banner', 'contentinfo', 'complementary', 'form', 'search',
    'region', 'table', 'row', 'cell', 'columnheader', 'rowheader',
    'img', 'figure', 'alert', 'dialog', 'status', 'progressbar',
    'separator', 'toolbar', 'tablist', 'tabpanel', 'tree', 'treegrid',
    'grid', 'gridcell', 'group', 'document', 'application',
})

# 纯文本角色：父节点 name 已包含其内容，渲染时跳过
_TEXT_ROLES = frozenset({
    'StaticText', 'InlineTextBox',
})

# 叶级内容角色：直接包含文本，需要从 StaticText 子节点收集文本
_CONTENT_ROLES = frozenset({
    'paragraph', 'code', 'heading', 'listitem', 'cell', 'columnheader',
    'rowheader', 'definition', 'term', 'caption', 'blockquote', 'LabelText',
    'legend', 'LineBreak',
})

# 获得 ref 编号的内容角色（有意义的内容块，AI 可通过编号引用提取）
_REF_CONTENT_ROLES = frozenset({
    'heading', 'paragraph', 'code', 'blockquote', 'article', 'figure',
})


def take_a11y_snapshot(page, selector=None, max_depth=None,
                       with_clickables: bool = True,
                       include_low: bool = False,
                       viewport_only: bool = False) -> dict:
    """
    获取页面 a11y tree，并（可选）合并 Vimium 风格的可点击元素探测。

    :param page: DrissionPage 的 ChromiumPage 对象
    :param selector: CSS 选择器，限定子树范围（可选）
    :param max_depth: 最大深度限制（可选，传给 CDP）
    :param with_clickables: True 时额外运行 clickable 探测并合并到快照；
                            收集 a11y tree 漏掉的可交互元素（如纯图标按钮、
                            弹窗菜单项等）
    :param include_low: with_clickables=True 时，是否包含 low 置信度元素
                        （cursor:pointer 或 class-pattern 启发式匹配）
    :param viewport_only: with_clickables=True 时，是否只探测视口内可见元素
    :return: 标准化的 a11y tree 数据；若 with_clickables=True，
             额外带 'clickable_extras' 字段（补充 a11y tree 未覆盖的可交互元素）
    """
    page.wait.doc_loaded()
    page_info = {'url': page.url, 'title': page.title}

    # ── 尝试 CDP 获取 ──
    try:
        flat_nodes = _get_full_tree_cdp(page, max_depth)
        normalized = [_normalize_node(n) for n in flat_nodes]
        tree = _build_tree(normalized)

        # 如果指定 selector，找到对应子树
        selector_warning = None
        if selector:
            tree, selector_warning = _find_subtree_by_selector(page, tree, normalized, selector)

        stats = _compute_stats(normalized)

        # 为交互节点 + 可引用内容节点批量生成定位器
        need_locator = [n for n in normalized
                        if n.get('backendNodeId') and (
                            n['role'] in _INTERACTIVE_ROLES or
                            n['role'] in _REF_CONTENT_ROLES)]
        _generate_locators_batch(page, need_locator)

        result = {
            'page': page_info,
            'mode': 'a11y',
            'method': 'cdp',
            'tree': tree,
            'stats': stats,
        }
        if selector_warning:
            result['warning'] = selector_warning

        # ── 可选：合并 clickable 探测结果 ──
        # 注意：clickable 必须自己建 bid_map —— 它的 JS 会给元素加
        # data-dp-scan-id 临时属性，bid_map 必须在那之后再建才能包含 scan-id
        if with_clickables:
            try:
                from .clickable import detect_clickables
                clk = detect_clickables(
                    page,
                    viewport_only=viewport_only,
                    include_low=include_low,
                )
                # 收集 a11y tree 已覆盖的 backendNodeId（有 locator 的交互节点）
                covered = {n['backendNodeId'] for n in normalized
                           if n.get('backendNodeId')
                           and n.get('locator')
                           and n['role'] in _INTERACTIVE_ROLES}
                # 过滤出 a11y 未覆盖的元素
                extras = [e for e in clk.get('elements', [])
                          if not (e.get('backendNodeId')
                                  and e['backendNodeId'] in covered)]
                # 过滤策略：如果有 rect 且 w/h < 2，跳过（已在 JS 过滤过，双保险）
                extras = [e for e in extras
                          if e.get('rect') and e['rect'].get('w', 0) >= 2]
                result['clickable_extras'] = extras
                result['clickable_meta'] = {
                    'total_detected': clk.get('total', 0),
                    'covered_by_a11y': clk.get('total', 0) - len(extras),
                    'extras': len(extras),
                    'truncated': clk.get('truncated', False),
                    'viewport_only': viewport_only,
                    'include_low': include_low,
                }
            except Exception as ce:
                result['clickable_warning'] = f'clickable 探测失败（非致命）：{ce}'

        return result
    except Exception as cdp_err:
        cdp_error_msg = str(cdp_err)

        # ── CDP 失败，降级到 JS fallback ──
        try:
            from .js_scripts import _JS_A11Y_FALLBACK
            raw = page.run_js(_JS_A11Y_FALLBACK)
            if isinstance(raw, dict):
                tree = raw.get('tree', {})
                stats = raw.get('stats', {})
                return {
                    'page': page_info,
                    'mode': 'a11y',
                    'method': 'js_fallback',
                    'tree': tree,
                    'stats': stats,
                    'warning': f'CDP 不可用，已降级到 JS fallback (CDP: {cdp_error_msg})',
                }
        except Exception:
            pass

    # ── 全部失败 ──
    return {
        'page': page_info,
        'mode': 'a11y',
        'method': 'failed',
        'tree': {},
        'stats': {'total': 0, 'ignored': 0, 'interactive': 0},
        'error': f'a11y tree 获取失败 (CDP: {cdp_err})',
    }


def render_a11y_text(snapshot: dict, verbose: bool = False,
                     brief: bool = False, refs: dict = None) -> str:
    """
    将 a11y tree 数据渲染为人类/AI 可读文本。

    :param snapshot: take_a11y_snapshot 返回的数据
    :param verbose: True 时显示 ignored 节点和完整属性
    :param brief: True 时截断内容文本，保留结构+交互，省 token
    :param refs: 可选，传入空 dict 时会被填充为 {ref_id: {locator, role, name, backendNodeId}}
    :return: 格式化的文本
    """
    lines = []
    page_info = snapshot.get('page', {})
    stats = snapshot.get('stats', {})

    # 渲染上下文：编号计数器 + ref 映射收集
    ctx = {'counter': 0, 'refs': {} if refs is None else refs}

    mode_label = 'brief' if brief else 'full'
    # 头部信息先占位，渲染完成后回填 ref 统计
    header_idx = len(lines)
    lines.append('')  # placeholder
    lines.append(f"- URL: {page_info.get('url', '')}")
    lines.append(f"- Title: {page_info.get('title', '')}")
    stats_idx = len(lines)
    lines.append('')  # placeholder for stats line
    if brief:
        lines.append('- Note: 内容已精简，如需完整文本请用 --mode full 或 --selector')
    lines.append('')

    if snapshot.get('warning'):
        lines.append(f"⚠ {snapshot['warning']}")
    if snapshot.get('error'):
        lines.append(f"⚠ {snapshot['error']}")
        lines[header_idx] = f'### Page Snapshot ({mode_label})'
        lines[stats_idx] = (f"- Nodes: {stats.get('total', 0)} total, "
                            f"{stats.get('interactive', 0)} interactive")
        return '\n'.join(lines)

    tree = snapshot.get('tree', {})
    if tree:
        _render_node(tree, lines, depth=0, verbose=verbose, brief=brief,
                     ctx=ctx)
    else:
        lines.append('（a11y tree 为空）')

    # ── 追加 clickable_extras（a11y tree 漏掉的可交互元素）──
    extras = snapshot.get('clickable_extras') or []
    if extras:
        from .clickable import CONFIDENCE_MARKER
        lines.append('')
        meta = snapshot.get('clickable_meta') or {}
        header_suffix = []
        if meta.get('viewport_only'):
            header_suffix.append('viewport-only')
        if meta.get('include_low'):
            header_suffix.append('include-low')
        suffix_str = (f' — {", ".join(header_suffix)}'
                      if header_suffix else '')
        lines.append(f'### Additional Interactive Elements'
                     f' (Vimium-style, not in a11y tree){suffix_str}')
        lines.append(f'- 共 {len(extras)} 个；⚡ = medium 置信, ? = low 置信；'
                     f'用 ref:N 引用')
        lines.append('')
        for rec in extras:
            ctx['counter'] += 1
            rid = ctx['counter']
            marker = CONFIDENCE_MARKER.get(rec.get('confidence'), '')
            tag = rec.get('tag', '')
            text = (rec.get('text') or '').strip()
            reason = rec.get('reason') or ''
            loc = rec.get('locator') or ''
            rect = rec.get('rect') or {}

            parts = [f'- [{rid}] {marker}{tag}']
            if text:
                display_text = text[:80] + '…' if len(text) > 80 else text
                parts.append(f'"{display_text}"')
            meta_parts = [reason]
            if rect.get('w'):
                meta_parts.append(f'{rect["w"]}x{rect["h"]}')
            parts.append(f'({", ".join(meta_parts)})')
            if loc:
                parts.append(f'→ {loc}')
            lines.append(' '.join(parts))

            # 记入 refs 以便 click/fill 引用
            ctx['refs'][str(rid)] = {
                'locator': loc,
                'role': f'clickable/{tag}',
                'name': text[:100],
                'backendNodeId': rec.get('backendNodeId'),
                'confidence': rec.get('confidence'),
                'reason': reason,
            }

    if snapshot.get('clickable_warning'):
        lines.append('')
        lines.append(f"⚠ {snapshot['clickable_warning']}")

    # 回填头部：包含 ref 统计
    ref_count = ctx['counter']
    lines[header_idx] = f'### Page Snapshot ({mode_label})'
    stats_line = (f"- Nodes: {stats.get('total', 0)} total, "
                  f"{stats.get('interactive', 0)} interactive, "
                  f"{ref_count} refs")
    if extras:
        stats_line += f" (含 {len(extras)} 个 a11y 外可交互)"
    lines[stats_idx] = stats_line
    if ref_count > 0:
        lines[stats_idx] += f" — 使用 ref:N 引用元素，如 dp click \"ref:1\""

    # 如果调用方传了 refs dict，确保数据已填充
    if refs is not None:
        refs.update(ctx['refs'])

    return '\n'.join(lines)


def render_a11y_plain_text(snapshot: dict, refs: dict = None) -> str:
    """
    将 a11y tree 扁平化为纯文本（按阅读顺序）。

    :param snapshot: take_a11y_snapshot 返回的数据
    :param refs: 可选，传入空 dict 时会被填充为 ref 映射（避免需要额外调用 render_a11y_text）
    :return: 纯文本字符串
    """
    tree = snapshot.get('tree', {})

    # 如果需要收集 refs，在纯文本渲染过程中顺便收集
    if refs is not None:
        ctx = {'counter': 0, 'refs': refs}
        if tree:
            _collect_refs_only(tree, ctx)
        # 合并 clickable_extras 的 refs（与 full/brief 保持编号一致）
        for rec in snapshot.get('clickable_extras') or []:
            ctx['counter'] += 1
            rid = ctx['counter']
            ctx['refs'][str(rid)] = {
                'locator': rec.get('locator') or '',
                'role': f"clickable/{rec.get('tag', '')}",
                'name': (rec.get('text') or '')[:100],
                'backendNodeId': rec.get('backendNodeId'),
                'confidence': rec.get('confidence'),
                'reason': rec.get('reason'),
            }
        refs.update(ctx['refs'])

    if not tree:
        return ''

    parts = []
    _collect_plain_text(tree, parts)
    return '\n'.join(parts)


# ── CDP 获取函数 ──────────────────────────────────────────────────────────────


def _get_full_tree_cdp(page, max_depth=None) -> list:
    """通过 CDP getFullAXTree 获取完整 a11y tree"""
    kwargs = {}
    if max_depth is not None:
        kwargs['depth'] = max_depth
    result = page.run_cdp('Accessibility.getFullAXTree', **kwargs)
    return result.get('nodes', [])


def _find_subtree_by_selector(page, tree: dict, all_nodes: list,
                               selector: str) -> tuple:
    """在已构建的 a11y tree 中，找到 selector 对应的子树。

    :return: (subtree, warning) — subtree 为匹配的子树或完整树，warning 为失败提示或 None
    """
    # 1. 获取 selector 对应的 backendNodeId
    try:
        doc = page.run_cdp('DOM.getDocument')
        root_id = doc['root']['nodeId']
        result = page.run_cdp('DOM.querySelector', nodeId=root_id, selector=selector)
        node_id = result.get('nodeId')
        if not node_id:
            return tree, f'--selector "{selector}" 未匹配到元素，已返回完整页面快照'

        desc = page.run_cdp('DOM.describeNode', nodeId=node_id)
        target_bid = desc['node']['backendNodeId']
    except Exception:
        return tree, f'--selector "{selector}" 查询失败，已返回完整页面快照'

    # 2. 在 a11y tree 中查找匹配的节点
    def find_node(node, target_bid):
        if node.get('backendNodeId') == target_bid:
            return node
        for child in node.get('children', []):
            found = find_node(child, target_bid)
            if found:
                return found
        return None

    subtree = find_node(tree, target_bid)
    if subtree:
        return subtree, None
    return tree, f'--selector "{selector}" 在 a11y tree 中未找到对应节点，已返回完整页面快照'


# ── 数据标准化 ────────────────────────────────────────────────────────────────


def _normalize_node(raw: dict) -> dict:
    """解包 CDP AXValue 对象为简单值"""
    return {
        'nodeId': raw.get('nodeId', ''),
        'role': _ax_value(raw.get('role')),
        'name': _ax_value(raw.get('name')),
        'description': _ax_value(raw.get('description')),
        'value': _ax_value(raw.get('value')),
        'ignored': raw.get('ignored', False),
        'ignoredReasons': [
            _ax_value(r) for r in raw.get('ignoredReasons', [])
        ] if raw.get('ignoredReasons') else [],
        'properties': {
            p['name']: _ax_value(p.get('value'))
            for p in raw.get('properties', [])
        },
        'childIds': raw.get('childIds', []),
        'parentId': raw.get('parentId'),
        'backendNodeId': raw.get('backendDOMNodeId'),
        'frameId': raw.get('frameId'),
        # 后续填充
        'locator': None,
        'children': [],
    }


def _ax_value(v) -> any:
    """从 AXValue 对象中提取值"""
    if v is None:
        return ''
    if isinstance(v, dict):
        return v.get('value', '')
    return v


# ── 构建树 ────────────────────────────────────────────────────────────────────


def _build_tree(flat_nodes: list) -> dict:
    """将扁平节点列表按 parentId/childIds 关系组装为嵌套树"""
    if not flat_nodes:
        return {}

    node_map = {n['nodeId']: n for n in flat_nodes}

    for node in flat_nodes:
        children = []
        for cid in node.get('childIds', []):
            child = node_map.get(cid)
            if child:
                children.append(child)
        node['children'] = children

    # 根节点：没有 parentId 或 parentId 不在列表中的节点
    roots = [n for n in flat_nodes
             if not n.get('parentId') or n['parentId'] not in node_map]

    return roots[0] if roots else flat_nodes[0] if flat_nodes else {}


def _compute_stats(nodes: list) -> dict:
    """计算统计信息"""
    total = len(nodes)
    ignored = sum(1 for n in nodes if n.get('ignored'))
    interactive = sum(1 for n in nodes if n['role'] in _INTERACTIVE_ROLES)
    return {
        'total': total,
        'ignored': ignored,
        'interactive': interactive,
    }


# ── 定位器生成 ────────────────────────────────────────────────────────────────


def _generate_locators_batch(page, interactive_nodes: list) -> None:
    """批量为交互节点生成 DrissionPage 定位器。

    优化：一次 DOM.getDocument(depth=-1) 获取完整 DOM 树，
    再从内存中按 backendNodeId 查找，避免 N 次 CDP 往返。
    """
    if not interactive_nodes:
        return

    # 收集需要的 backendNodeId
    bid_to_nodes = {}
    for node in interactive_nodes:
        bid = node.get('backendNodeId')
        if bid:
            bid_to_nodes.setdefault(bid, []).append(node)

    if not bid_to_nodes:
        return

    # 方案 1：一次性获取完整 DOM 树并建索引
    bid_map = _build_dom_bid_map(page)

    if bid_map:
        for bid, nodes in bid_to_nodes.items():
            dom_info = bid_map.get(bid)
            if dom_info:
                text = (nodes[0].get('name') or '')[:50]
                loc = suggest_locator(dom_info['tag'], dom_info['attrs'], text)
                for n in nodes:
                    n['locator'] = loc
    else:
        # fallback：逐个查询（兼容 DOM.getDocument 不可用的情况）
        for bid, nodes in bid_to_nodes.items():
            dom_info = _get_dom_attrs(page, bid)
            if dom_info:
                text = (nodes[0].get('name') or '')[:50]
                loc = suggest_locator(dom_info['tag'], dom_info['attrs'], text)
                for n in nodes:
                    n['locator'] = loc


def _build_dom_bid_map(page) -> dict:
    """一次性获取完整 DOM 树，返回 {backendNodeId: {tag, attrs}} 映射"""
    try:
        doc = page.run_cdp('DOM.getDocument', depth=-1)
        bid_map = {}
        _walk_dom_node(doc.get('root', {}), bid_map)
        return bid_map
    except Exception:
        return {}


def _walk_dom_node(node: dict, bid_map: dict) -> None:
    """递归遍历 DOM 节点，建立 backendNodeId → {tag, attrs} 索引"""
    bid = node.get('backendNodeId')
    if bid:
        attrs_list = node.get('attributes', [])
        attrs = dict(zip(attrs_list[::2], attrs_list[1::2]))
        bid_map[bid] = {
            'tag': node.get('nodeName', '').lower(),
            'attrs': attrs,
        }
    for child in node.get('children', []):
        _walk_dom_node(child, bid_map)
    # shadow DOM / content document
    for sub in node.get('shadowRoots', []):
        _walk_dom_node(sub, bid_map)
    cd = node.get('contentDocument')
    if cd:
        _walk_dom_node(cd, bid_map)


def _get_dom_attrs(page, backend_node_id: int) -> dict:
    """通过 CDP DOM.describeNode 获取 DOM 节点的属性（fallback 逐个查询）"""
    try:
        result = page.run_cdp('DOM.describeNode', backendNodeId=backend_node_id)
        node = result.get('node', {})
        attrs_list = node.get('attributes', [])
        attrs = dict(zip(attrs_list[::2], attrs_list[1::2]))
        return {
            'tag': node.get('nodeName', '').lower(),
            'attrs': attrs,
        }
    except Exception:
        return {}


# ── 文本渲染 ──────────────────────────────────────────────────────────────────


def _render_node(node: dict, lines: list, depth: int = 0,
                 verbose: bool = False, parent_text: str = '',
                 brief: bool = False, ctx: dict = None) -> None:
    """递归渲染单个 a11y 节点为文本行

    :param parent_text: 父节点已显示的文本，用于消除子节点冗余
    :param brief: True 时截断内容文本（paragraph/code 等）
    :param ctx: 渲染上下文 {'counter': int, 'refs': dict}，用于分配 [N] 编号
    """
    role = node.get('role', '')
    name = node.get('name', '')
    ignored = node.get('ignored', False)
    children = node.get('children', [])

    # 跳过 ignored 节点（除非 verbose），但仍然渲染子节点
    if ignored and not verbose:
        for child in children:
            _render_node(child, lines, depth, verbose=verbose,
                         parent_text=parent_text, brief=brief, ctx=ctx)
        return

    # 跳过 InlineTextBox（永远是 StaticText 的子节点，完全冗余）
    if role == 'InlineTextBox':
        return

    # StaticText 特殊处理：如果文本已被父节点覆盖则跳过
    if role == 'StaticText':
        text = name.strip()
        if not text or (parent_text and text in parent_text):
            return
        # brief 模式：跳过独立文本节点（正文细节，概览不需要）
        if brief:
            return
        # 独立文本节点：直接输出文本内容（不显示 StaticText 角色名）
        indent = '  ' * depth
        lines.append(f'{indent}- "{text}"')
        return

    # 如果节点没有 name，且是叶级内容角色，从子节点收集文本
    display_name = name
    if not display_name and role in _CONTENT_ROLES:
        display_name = _collect_text(node)

    # 跳过无意义的容器节点（generic/none 且没有名字）
    if role in ('generic', 'none', '') and not name:
        for child in children:
            _render_node(child, lines, depth, verbose=verbose,
                         parent_text=parent_text, brief=brief, ctx=ctx)
        return

    # 文本与父节点重复时，跳过纯文本包装节点（无结构子节点的小包装）
    if (display_name and parent_text and display_name in parent_text
            and role not in _INTERACTIVE_ROLES):
        has_structural_child = any(
            c.get('role', '') not in _TEXT_ROLES for c in children)
        if not has_structural_child:
            return

    # ── 判断是否分配 ref 编号 ──
    ref_label = ''
    loc = node.get('locator')
    if ctx is not None:
        should_ref = False
        if role in _INTERACTIVE_ROLES and loc:
            should_ref = True
        elif role in _REF_CONTENT_ROLES and display_name:
            should_ref = True
        if should_ref:
            ctx['counter'] += 1
            ref_id = ctx['counter']
            ref_label = f'[{ref_id}] '
            ctx['refs'][str(ref_id)] = {
                'locator': loc,
                'role': role,
                'name': (display_name or name or '')[:100],
                'backendNodeId': node.get('backendNodeId'),
            }

    # 构建行内容
    indent = '  ' * depth
    parts = []

    # 角色
    if role:
        parts.append(role)

    # 名字/文本（brief 模式下截断内容角色的文本）
    shown_name = display_name
    if brief and shown_name and role in _CONTENT_ROLES and len(shown_name) > 80:
        shown_name = shown_name[:80] + '...'
    if shown_name:
        parts.append(f'"{shown_name}"')

    # 关键属性
    props = node.get('properties', {})
    prop_strs = []
    for key in ('checked', 'expanded', 'selected', 'disabled', 'required',
                'level', 'pressed', 'valuetext'):
        val = props.get(key)
        if val is not None and val != '' and val is not False:
            if val is True:
                prop_strs.append(key)
            else:
                prop_strs.append(f'{key}={val}')

    if prop_strs:
        parts.append(f"[{', '.join(prop_strs)}]")

    # value（输入框等）
    value = node.get('value', '')
    if value and role in ('textbox', 'combobox', 'slider', 'spinbutton', 'searchbox'):
        parts.append(f'value="{value}"')

    # 定位器
    if loc:
        parts.append(f'→ {loc}')

    # ignored 标记
    if ignored:
        parts.append('[ignored]')

    # description
    desc = node.get('description', '')
    if desc and verbose:
        parts.append(f'desc="{desc}"')

    if parts:
        lines.append(f"{indent}- {ref_label}{' '.join(parts)}")

    # 递归渲染子节点，传递当前节点的文本上下文
    text_ctx = display_name or parent_text
    for child in children:
        _render_node(child, lines, depth + 1, verbose=verbose,
                     parent_text=text_ctx, brief=brief, ctx=ctx)


def _collect_text(node: dict, _depth: int = 0) -> str:
    """从节点子树中收集可见文本。

    递归穿透无名 generic/none 容器，收集 StaticText 和有 name 的子节点文本。
    深度限制宽松（10 层），确保文章内容完整收集；截断由渲染层（brief 模式）控制。
    """
    if _depth > 10:
        return ''
    parts = []
    for child in node.get('children', []):
        child_role = child.get('role', '')
        child_name = child.get('name', '')
        if child_role in _TEXT_ROLES:
            if child_name:
                parts.append(child_name)
        elif child_name:
            # 有名字的子节点（link/code 等）贡献文本
            parts.append(child_name)
        elif child_role in ('generic', 'none', ''):
            # 无名容器：递归穿透，收集其内部文本
            sub = _collect_text(child, _depth + 1)
            if sub:
                parts.append(sub)
    if parts:
        return ''.join(parts).strip()
    return ''


# ── 纯文本渲染 ──────────────────────────────────────────────────────────────────────


# 块级角色：渲染纯文本时在前后插入换行
_BLOCK_ROLES = frozenset({
    'paragraph', 'heading', 'listitem', 'code', 'blockquote',
    'figure', 'separator', 'article', 'main', 'banner', 'contentinfo',
    'navigation', 'complementary', 'search', 'region', 'form',
})


def _collect_plain_text(node: dict, parts: list) -> None:
    """递归收集节点的可见文本（按阅读顺序）"""
    role = node.get('role', '')
    name = node.get('name', '')
    children = node.get('children', [])

    if node.get('ignored', False):
        for child in children:
            _collect_plain_text(child, parts)
        return

    if role in _TEXT_ROLES:
        text = name.strip()
        if text:
            parts.append(text)
        return

    if role == 'InlineTextBox':
        return

    # 块级元素：收集完子节点后加换行
    is_block = role in _BLOCK_ROLES

    for child in children:
        _collect_plain_text(child, parts)

    if is_block and parts and parts[-1] != '':
        parts.append('')  # 空行分隔块级元素


def _collect_refs_only(node: dict, ctx: dict) -> None:
    """轻量遍历树，只分配 ref 编号（不渲染任何输出）"""
    role = node.get('role', '')
    name = node.get('name', '')
    ignored = node.get('ignored', False)
    children = node.get('children', [])
    loc = node.get('locator')

    if ignored:
        for child in children:
            _collect_refs_only(child, ctx)
        return

    if role in ('InlineTextBox', 'StaticText'):
        return

    # 与 _render_node 同逻辑判断是否分配编号
    display_name = name
    if not display_name and role in _CONTENT_ROLES:
        display_name = _collect_text(node)

    should_ref = False
    if role in _INTERACTIVE_ROLES and loc:
        should_ref = True
    elif role in _REF_CONTENT_ROLES and display_name:
        should_ref = True

    if should_ref:
        ctx['counter'] += 1
        ref_id = ctx['counter']
        ctx['refs'][str(ref_id)] = {
            'locator': loc,
            'role': role,
            'name': (display_name or name or '')[:100],
            'backendNodeId': node.get('backendNodeId'),
        }

    for child in children:
        _collect_refs_only(child, ctx)
