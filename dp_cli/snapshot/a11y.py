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


def take_a11y_snapshot(page, selector=None, max_depth=None) -> dict:
    """
    获取页面 a11y tree。

    :param page: DrissionPage 的 ChromiumPage 对象
    :param selector: CSS 选择器，限定子树范围（可选）
    :param max_depth: 最大深度限制（可选，传给 CDP）
    :return: 标准化的 a11y tree 数据
    """
    page.wait.doc_loaded()
    page_info = {'url': page.url, 'title': page.title}

    # ── 尝试 CDP 获取 ──
    try:
        flat_nodes = _get_full_tree_cdp(page, max_depth)
        normalized = [_normalize_node(n) for n in flat_nodes]
        tree = _build_tree(normalized)

        # 如果指定 selector，找到对应子树
        if selector:
            tree = _find_subtree_by_selector(page, tree, normalized, selector)

        stats = _compute_stats(normalized)

        # 为交互节点批量生成定位器
        interactive = [n for n in normalized
                       if n['role'] in _INTERACTIVE_ROLES and n.get('backendNodeId')]
        _generate_locators_batch(page, interactive)

        return {
            'page': page_info,
            'mode': 'a11y',
            'method': 'cdp',
            'tree': tree,
            'stats': stats,
        }
    except Exception as cdp_err:
        pass

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
                     brief: bool = False) -> str:
    """
    将 a11y tree 数据渲染为人类/AI 可读文本。

    :param snapshot: take_a11y_snapshot 返回的数据
    :param verbose: True 时显示 ignored 节点和完整属性
    :param brief: True 时截断内容文本，保留结构+交互，省 token
    :return: 格式化的文本
    """
    lines = []
    page_info = snapshot.get('page', {})
    stats = snapshot.get('stats', {})
    method = snapshot.get('method', 'unknown')

    mode_label = 'brief' if brief else 'full'
    lines.append(f'### Page Snapshot ({mode_label})')
    lines.append(f"- URL: {page_info.get('url', '')}")
    lines.append(f"- Title: {page_info.get('title', '')}")
    lines.append(f"- Nodes: {stats.get('total', 0)} total, "
                 f"{stats.get('interactive', 0)} interactive")
    if brief:
        lines.append('- Note: 内容已精简，如需完整文本请用 --mode full 或 --selector')
    lines.append('')

    if snapshot.get('error'):
        lines.append(f"⚠ {snapshot['error']}")
        return '\n'.join(lines)

    tree = snapshot.get('tree', {})
    if tree:
        _render_node(tree, lines, depth=0, verbose=verbose, brief=brief)
    else:
        lines.append('（a11y tree 为空）')

    return '\n'.join(lines)


def render_a11y_plain_text(snapshot: dict) -> str:
    """
    将 a11y tree 扁平化为纯文本（按阅读顺序）。

    :param snapshot: take_a11y_snapshot 返回的数据
    :return: 纯文本字符串
    """
    tree = snapshot.get('tree', {})
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
                               selector: str) -> dict:
    """在已构建的 a11y tree 中，找到 selector 对应的子树"""
    # 1. 获取 selector 对应的 backendNodeId
    try:
        doc = page.run_cdp('DOM.getDocument')
        root_id = doc['root']['nodeId']
        result = page.run_cdp('DOM.querySelector', nodeId=root_id, selector=selector)
        node_id = result.get('nodeId')
        if not node_id:
            return tree  # 未找到，返回完整树

        desc = page.run_cdp('DOM.describeNode', nodeId=node_id)
        target_bid = desc['node']['backendNodeId']
    except Exception:
        return tree

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
    return subtree if subtree else tree


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
    """批量为交互节点生成 DrissionPage 定位器"""
    for node in interactive_nodes:
        bid = node.get('backendNodeId')
        if not bid:
            continue
        dom_info = _get_dom_attrs(page, bid)
        if dom_info:
            tag = dom_info.get('tag', '')
            attrs = dom_info.get('attrs', {})
            text = (node.get('name') or '')[:50]
            loc = suggest_locator(tag, attrs, text)
            node['locator'] = loc


def _get_dom_attrs(page, backend_node_id: int) -> dict:
    """通过 CDP DOM.describeNode 获取 DOM 节点的属性"""
    try:
        result = page.run_cdp('DOM.describeNode', backendNodeId=backend_node_id)
        node = result.get('node', {})
        # attributes 是 ["key1", "val1", "key2", "val2", ...] 交替列表
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
                 brief: bool = False) -> None:
    """递归渲染单个 a11y 节点为文本行

    :param parent_text: 父节点已显示的文本，用于消除子节点冗余
    :param brief: True 时截断内容文本（paragraph/code 等）
    """
    role = node.get('role', '')
    name = node.get('name', '')
    ignored = node.get('ignored', False)
    children = node.get('children', [])

    # 跳过 ignored 节点（除非 verbose），但仍然渲染子节点
    if ignored and not verbose:
        for child in children:
            _render_node(child, lines, depth, verbose=verbose,
                         parent_text=parent_text, brief=brief)
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
                         parent_text=parent_text, brief=brief)
        return

    # 文本与父节点重复时，跳过纯文本包装节点（无结构子节点的小包装）
    if (display_name and parent_text and display_name in parent_text
            and role not in _INTERACTIVE_ROLES):
        has_structural_child = any(
            c.get('role', '') not in _TEXT_ROLES for c in children)
        if not has_structural_child:
            return

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
    loc = node.get('locator')
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
        lines.append(f"{indent}- {' '.join(parts)}")

    # 递归渲染子节点，传递当前节点的文本上下文
    ctx = display_name or parent_text
    for child in children:
        _render_node(child, lines, depth + 1, verbose=verbose,
                     parent_text=ctx, brief=brief)


def _collect_text(node: dict, _depth: int = 0) -> str:
    """从节点子树中收集可见文本。

    递归穿透无名 generic/none 容器，收集 StaticText 和有 name 的子节点文本。
    限制最大深度和长度，防止容器获得巨大文本 blob。
    """
    if _depth > 3:
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
