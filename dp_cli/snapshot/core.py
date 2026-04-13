# -*- coding:utf-8 -*-
"""
take_snapshot — 页面快照核心函数

新设计（v2）：
  default 模式 = 结构分析 + 可交互元素按区域分组 + 主体内容
  交互元素不再是扁平列表，而是按页面区域（导航栏、搜索区、列表区、页脚）分组
  自动检测重复模式（列表），生成自动化提示
"""
from .js_scripts import _JS_PAGE_STRUCTURE, _JS_CONTENT_BLOCKS, _JS_INTERACTIVE


def take_snapshot(page, mode: str = 'default',
                  selector: str = None,
                  max_depth: int = 12,
                  min_text: int = 2,
                  max_text: int = 2000,
                  show_tree: bool = False) -> dict:
    """
    生成页面快照。

    :param mode:
        'default'     结构分析 + 可交互元素（按区域分组） + 主体内容（推荐）
        'interactive' 只输出可交互元素（按区域分组）
        'content'     只输出主体内容区块
        'text'        页面全量纯文本
    :param show_tree: 已废弃（default 模式自带结构），保留参数兼容
    """
    page.wait.doc_loaded()
    page_info = {'url': page.url, 'title': page.title}

    if mode == 'text':
        root = page.s_ele(selector) if selector else page.s_ele()
        text = root.text if root else ''
        return {'page': page_info, 'mode': 'text', 'text': text}

    # ── default / interactive / content ──────────────────────────────────────
    structure = None
    content_data = {}

    if mode in ('default', 'interactive'):
        try:
            structure = page.run_js(_JS_PAGE_STRUCTURE)
        except Exception as e:
            # 降级：用扁平列表
            structure = None
            try:
                raw = page.run_js(_JS_INTERACTIVE)
                flat_elements = raw.get('interactive', []) if isinstance(raw, dict) else []
                structure = _flat_to_structure(flat_elements)
            except Exception:
                pass

    if mode in ('default', 'content'):
        try:
            raw = page.run_js(_JS_CONTENT_BLOCKS)
            if isinstance(raw, dict):
                content_data = raw
            else:
                content_data = {'nodes': [], 'error': f'unexpected result: {type(raw)}'}
        except Exception as e:
            content_data = {'nodes': [], 'error': str(e)}

    return {
        'page': page_info,
        'mode': mode,
        'structure': structure,
        'content_data': content_data,
    }


def _flat_to_structure(elements: list) -> dict:
    """将扁平交互元素列表转换为简单结构（降级方案）"""
    nav_els = [e for e in elements if e.get('in_nav')]
    main_els = [e for e in elements if not e.get('in_nav')]

    sections = []
    if nav_els:
        sections.append({
            'tag': 'nav', 'type': 'header', 'label': '导航栏',
            'sectionId': 'nav',
            'interactiveCount': len(nav_els),
            'elements': nav_els,
        })
    if main_els:
        sections.append({
            'tag': 'main', 'type': 'main', 'label': '主体区域',
            'sectionId': 'main',
            'interactiveCount': len(main_els),
            'elements': main_els,
        })

    return {
        'tag': 'body', 'type': 'other', 'label': '页面',
        'sectionId': 'body',
        'interactiveCount': len(elements),
        'children': sections,
    }
