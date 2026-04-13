# -*- coding:utf-8 -*-
"""
render_snapshot_text — 将快照数据渲染为人类 / AI 可读文本

设计原则：
  1. 按页面区域分块输出，让 AI 一眼理解页面结构
  2. 导航栏/页脚等次要区域折叠显示（只显示元素数量和前几个）
  3. 列表区域突出显示，并给出自动化提示
  4. 内容区域输出 markdown 格式
"""

# 区域类型对应的 emoji 前缀
_TYPE_ICONS = {
    'header': '🧭', 'nav': '🧭', 'search': '🔍', 'filter': '🏷',
    'list': '📋', 'content': '📄', 'main': '📌',
    'sidebar': '📎', 'footer': '📎', 'other': '┌',
}

# 次要区域（折叠显示，限制元素数量）
_MINOR_TYPES = {'footer', 'sidebar', 'other'}

# 每个区域最多显示的元素数
_MAX_ELEMENTS_FULL = 20
_MAX_ELEMENTS_MINOR = 5


def render_snapshot_text(snapshot: dict) -> str:
    """将快照数据渲染为人类/AI 可读文本"""
    lines = []
    page_info = snapshot.get('page', {})
    lines.append("### 页面快照")
    lines.append(f"- URL: {page_info.get('url', '')}")
    lines.append(f"- Title: {page_info.get('title', '')}")
    lines.append('')

    mode = snapshot.get('mode', 'default')

    if mode == 'text':
        lines.append("### 页面文本")
        lines.append('')
        lines.append(snapshot.get('text', ''))
        return '\n'.join(lines)

    # ── default / interactive / content ──────────────────────────────────────

    structure = snapshot.get('structure')
    if structure and mode in ('default', 'interactive'):
        # 收集所有区域（展平为列表）
        sections = _flatten_sections(structure)
        total_elements = sum(len(s.get('elements', [])) for s in sections)

        # ── 后处理 ──
        for s in sections:
            p = s.get('pattern')
            # 过滤无效模式（隐藏元素如 .d-none, .hidden；无元素的 sticky 区域）
            if p and p.get('selector', ''):
                sel = p['selector'].lower()
                sid = s.get('sectionId', '').lower()
                if any(kw in sel for kw in ('none', 'hidden', 'invisible', 'sr-only')):
                    s['pattern'] = None
                    p = None
                elif not s.get('elements') and any(kw in sid for kw in ('sticky', 'fixed', 'overlay')):
                    s['pattern'] = None
                    p = None
            # 有重复模式的 other 区域升级为列表区
            # 但如果重复项很少（<=3）且文字很多，可能是文章区引用，不升级
            if p and s.get('type') == 'other':
                p_count = p.get('count', 0)
                text_len = s.get('textLen', 0)
                if p_count <= 3 and text_len > 5000:
                    # 少量重复 + 大量文字 = 可能是内容区，不升级
                    pass
                else:
                    s['type'] = 'list'
                    s['label'] = '列表区'

        # ── 过滤空区域：没有可展示元素也没有重复模式的区域移除 ──
        # 但保留 main/content 类型（代表主体区域，LLM 需要知道其存在）
        _KEEP_TYPES = {'main', 'content'}
        sections = [s for s in sections
                    if s.get('elements') or s.get('pattern')
                    or s.get('type') in _KEEP_TYPES]

        # ── 合并相邻导航区域（header 和 nav 都视为导航类） ──
        _NAV_TYPES = {'header', 'nav'}
        merged_sections = []
        for s in sections:
            st = s.get('type', 'other')
            if (merged_sections
                    and st in _NAV_TYPES
                    and merged_sections[-1].get('type') in _NAV_TYPES):
                # 合并到前一个同类区域
                prev = merged_sections[-1]
                prev_els = list(prev.get('elements', []))
                prev_els.extend(s.get('elements', []))
                prev['elements'] = prev_els
                prev['interactiveCount'] = prev.get('interactiveCount', 0) + s.get('interactiveCount', 0)
                sid = prev.get('sectionId', '')
                prev['sectionId'] = sid  # 保留第一个的标识
            else:
                merged_sections.append(s)
        sections = merged_sections

        # ── 过滤零散小区域 → 合并到「其他操作」 ──
        major_sections = []
        minor_elements = []
        for s in sections:
            els = s.get('elements', [])
            if (s.get('type') == 'other'
                    and len(els) <= 2
                    and not s.get('pattern')
                    and s.get('interactiveCount', 0) <= 2):
                minor_elements.extend(els)
            else:
                major_sections.append(s)

        # ── 自动推断页面类型 ──
        has_list = any(s.get('type') == 'list' for s in major_sections)
        has_article = any(s.get('type') == 'content' for s in major_sections)
        has_main = any(s.get('type') == 'main' for s in major_sections)
        has_search = any(s.get('type') == 'search' for s in major_sections)
        content_data = snapshot.get('content_data', {})
        content_nodes = content_data.get('nodes', [])
        content_text_len = sum(len(n.get('text', '')) for n in content_nodes)
        # 内容区不在 body 根节点且文本量大 → 真正的内容页
        root_tag = content_data.get('root_tag', '')
        is_content_page = (has_article or has_main
                           or (content_text_len > 1000 and root_tag != 'body'))
        page_types = []
        if has_list: page_types.append('列表页')
        if is_content_page: page_types.append('内容页')
        if has_search: page_types.append('含搜索')
        type_hint = ' | '.join(page_types) if page_types else '常规页面'

        lines.append(f"### 页面结构 ({len(major_sections)} 个区域, {total_elements} 个可交互元素) — {type_hint}")
        lines.append('')

        for section in major_sections:
            _render_section(section, lines)

        # 合并的零散元素
        if minor_elements:
            lines.append(f"#### ┌ 其他操作 ({len(minor_elements)}个元素)")
            for i, el in enumerate(minor_elements):
                _render_element(el, i, lines)
            lines.append('')

    elif mode in ('default', 'interactive'):
        lines.append("（结构分析失败，页面可能未加载完成）")
        lines.append('')

    # ── 内容区块 ──
    content_data = snapshot.get('content_data', {})
    nodes = content_data.get('nodes', [])
    if nodes and mode in ('default', 'content'):
        root_loc = content_data.get('root_loc', '')
        root_label = (content_data.get('root_id') or
                      content_data.get('root_cls') or
                      content_data.get('root_tag', ''))
        lines.append(f"### 主体内容  [{root_label}]  loc: {root_loc}")
        lines.append('')
        for node in nodes:
            tag = node.get('tag', 'p')
            text = node.get('text', '')
            if not text:
                continue
            if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                level = int(tag[1])
                lines.append(f"{'#' * level} {text}")
            elif tag == 'pre':
                lines.append('```')
                lines.append(text)
                lines.append('```')
            elif tag == 'li':
                lines.append(f"- {text}")
            elif tag == 'blockquote':
                for bline in text.split('\n'):
                    lines.append(f"> {bline}")
            else:
                lines.append(text)
            lines.append('')

    if not structure and not nodes:
        lines.append("（快照为空，页面可能未加载完成，请先 dp wait --loaded）")

    if content_data.get('error'):
        lines.append(f"⚠ 内容提取警告: {content_data['error']}")

    if 'error' in snapshot:
        lines.append(f"\n### Error")
        lines.append(snapshot['error'])

    return '\n'.join(lines)


def _flatten_sections(structure: dict, depth: int = 0) -> list:
    """将嵌套结构展平为区域列表，保留层级深度信息"""
    if not structure:
        return []

    result = []
    elements = structure.get('elements', [])
    children = structure.get('children', [])
    section_type = structure.get('type', 'other')

    # 根节点（body）：跳过自身，直接处理子节点
    if depth == 0 and structure.get('tag') == 'body':
        for child in children:
            result.extend(_flatten_sections(child, depth))
        # 根节点自身的元素归入"其他"
        if elements:
            result.append({**structure, 'children': None, 'depth': depth})
        return result

    # header/footer/nav/search 类型：合并所有子区域元素到自身，不再拆分
    if section_type in ('header', 'footer', 'nav', 'search'):
        all_elements = _collect_all_elements(structure)
        merged = {**structure, 'children': None, 'depth': depth, 'elements': all_elements}
        merged['interactiveCount'] = len(all_elements)
        result.append(merged)
        return result

    # 有元素或有重复模式的区域：输出
    has_content = elements or structure.get('pattern')
    if has_content:
        result.append({**structure, 'children': None, 'depth': depth})

    # 如果检测到重复模式，不再递归子区域（避免把列表项拆成 N 个区域）
    if structure.get('pattern'):
        return result

    # 递归处理子区域
    for child in children:
        result.extend(_flatten_sections(child, depth + 1))

    # 如果自身无内容也无子输出：仍然标记为一个区域
    if not has_content and not result and structure.get('interactiveCount', 0) > 0:
        result.append({**structure, 'children': None, 'depth': depth})

    return result


def _collect_all_elements(structure: dict) -> list:
    """递归收集区域及其所有子区域的元素"""
    elements = list(structure.get('elements', []))
    for child in structure.get('children', []):
        elements.extend(_collect_all_elements(child))
    return elements


def _render_section(section: dict, lines: list) -> None:
    """渲染单个区域"""
    section_type = section.get('type', 'other')
    label = section.get('label', section_type)
    section_id = section.get('sectionId', '')
    icon = _TYPE_ICONS.get(section_type, '┌')
    elements = section.get('elements', [])
    pattern = section.get('pattern')
    interactive_count = section.get('interactiveCount', 0)
    is_minor = section_type in _MINOR_TYPES

    # 区域标题
    el_count = len(elements)
    count_str = f"{el_count}个元素" if el_count else f"{interactive_count}个交互元素"
    lines.append(f"#### {icon} {label} [{section_id}] ({count_str})")

    # main/content 区域无直接元素时，提示内容在子结构中
    if not elements and not pattern and section_type in ('main', 'content') and interactive_count > 0:
        lines.append(f"  ℹ️ 此区域包含页面主体内容（文件列表、文章正文等），共 {interactive_count} 个交互元素")

    # 重复模式提示
    if pattern:
        p_count = pattern.get('count', 0)
        p_sel = pattern.get('selector', '')
        p_fields = pattern.get('sampleFields', [])
        lines.append(f"  📊 检测到 {p_count} 条重复项 (容器: {p_sel})")
        if p_fields:
            field_names = ', '.join(f.get('cls', '') for f in p_fields[:5])
            lines.append(f"  字段: {field_names}")
        lines.append(f"  💡 批量提取: dp extract \"{p_sel}\" '{{...}}'")
        lines.append('')

    # 交互元素列表
    max_show = _MAX_ELEMENTS_MINOR if is_minor else _MAX_ELEMENTS_FULL
    for i, el in enumerate(elements[:max_show]):
        _render_element(el, i, lines)

    if len(elements) > max_show:
        lines.append(f"  ... 还有 {len(elements) - max_show} 个元素")

    lines.append('')


def _render_element(el: dict, idx: int, lines: list) -> None:
    """渲染单个交互元素"""
    tag = el.get('tag', '')
    role = el.get('role', tag)
    text = ' '.join(el.get('text', '').split())
    loc = ' '.join(el.get('loc', '').split())
    tp = el.get('type', '')
    ph = el.get('placeholder', '')

    parts = [f"  [{idx}]", f"<{tag}>"]
    if role and role != tag and role not in ('link', 'button', 'textbox'):
        parts.append(f"role={role}")
    if tp and tp not in ('submit', 'button', 'text'):
        parts.append(f"type={tp}")
    if text:
        parts.append(f'"{text[:60]}"')
    if ph:
        parts.append(f'ph="{ph[:40]}"')

    loc_str = f' → {loc}' if loc else ''
    lines.append(' '.join(parts) + loc_str)

