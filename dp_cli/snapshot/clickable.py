# -*- coding:utf-8 -*-
"""
Vimium-C 风格的可点击元素探测 — Python 侧调度

流程：
  1. run_js 注入 DETECT_CLICKABLES_JS，返回元素列表
     （每个元素已被打上 data-dp-scan-id="N" 属性）
  2. 获取（或复用） DOM 树 bid_map：backendNodeId → {tag, attrs}
  3. 按 data-dp-scan-id 把 JS 结果和 backendNodeId 关联
  4. 为每个元素生成 DrissionPage 定位器
  5. run_js 清理临时属性
  6. 返回 [ClickableRecord]
"""
from .clickable_js import (
    build_detect_js,
    CLEANUP_CLICKABLES_JS,
)
from .utils import suggest_locator


def detect_clickables(page,
                      viewport_only: bool = False,
                      max_elements: int = 1000,
                      include_low: bool = False) -> dict:
    """探测当前页面的可点击元素。

    流程：
      1. 运行探测 JS，给每个匹配元素打上 data-dp-scan-id 属性
      2. 通过 CDP DOM.getDocument 拉取整棵树，按 scan-id 反查 backendNodeId
      3. 清理 scan-id 属性

    :param page: DrissionPage ChromiumPage
    :param viewport_only: True 时只返回视口内元素
    :param max_elements: 最多返回多少个
    :param include_low: True 时包含 low 置信度（cursor:pointer / class-pattern）
    :return: {
        'elements': [ClickableRecord, ...],
        'total': int,
        'truncated': bool,
        'method': 'js+cdp' | 'failed',
        'viewport_only': bool,
        'include_low': bool,
    }
    """
    page.wait.doc_loaded()

    try:
        js = build_detect_js(viewport_only=viewport_only,
                             max_elements=max_elements,
                             include_low=include_low)
        raw = page.run_js(js)
    except Exception as e:
        return {
            'elements': [],
            'total': 0,
            'truncated': False,
            'method': 'failed',
            'error': f'clickable JS 执行失败：{e}',
            'viewport_only': viewport_only,
            'include_low': include_low,
        }

    if not isinstance(raw, dict):
        return {
            'elements': [],
            'total': 0,
            'truncated': False,
            'method': 'failed',
            'error': 'clickable JS 返回格式异常',
            'viewport_only': viewport_only,
            'include_low': include_low,
        }

    js_elements = raw.get('elements', []) or []
    truncated = bool(raw.get('truncated', False))

    # 建 bid_map（必须在 JS 打标之后，才能把 data-dp-scan-id 收入 attrs）
    bid_map = _build_bid_map_with_scan_id(page)
    # 索引：scanId (str) → (backendNodeId, attrs)
    scan_to_bid = {}
    for bid, info in bid_map.items():
        sid = (info.get('attrs') or {}).get('data-dp-scan-id')
        if sid:
            scan_to_bid[sid] = (bid, info)

    # 组装结果
    elements = []
    for e in js_elements:
        scan_id = str(e.get('scanId', ''))
        bid_info = scan_to_bid.get(scan_id)
        backend_node_id = None
        attrs = {}
        if bid_info:
            backend_node_id, info = bid_info
            attrs = info.get('attrs') or {}

        # 生成定位器
        text = (e.get('text') or '')[:50]
        # 去掉 data-dp-scan-id 再喂给 suggest_locator（临时属性不应出现在定位器里）
        clean_attrs = {k: v for k, v in attrs.items() if k != 'data-dp-scan-id'}
        loc = suggest_locator(e.get('tag', ''), clean_attrs, text)

        elements.append({
            'scanId': e.get('scanId'),
            'tag': e.get('tag'),
            'confidence': e.get('confidence'),
            'reason': e.get('reason'),
            'text': e.get('text'),
            'label': e.get('label'),
            'iconOnly': bool(e.get('iconOnly')),
            'zone': e.get('zone'),
            'rect': e.get('rect'),
            'inViewport': e.get('inViewport'),
            'backendNodeId': backend_node_id,
            'locator': loc,
        })

    # 清理临时属性（失败不致命，页面不会永久污染因为 detect 下次会重新赋号）
    try:
        page.run_js(CLEANUP_CLICKABLES_JS)
    except Exception:
        pass

    return {
        'elements': elements,
        'total': len(elements),
        'truncated': truncated,
        'method': 'js+cdp',
        'viewport_only': viewport_only,
        'include_low': include_low,
    }


def _build_bid_map_with_scan_id(page) -> dict:
    """与 a11y._build_dom_bid_map 等价，但这里独立实现一份，
    目的是避免循环 import，并保留同样的 shadow/iframe 穿透逻辑。
    """
    try:
        doc = page.run_cdp('DOM.getDocument', depth=-1, pierce=True)
        bid_map = {}
        _walk(doc.get('root', {}), bid_map)
        return bid_map
    except Exception:
        return {}


def _walk(node: dict, bid_map: dict) -> None:
    bid = node.get('backendNodeId')
    if bid:
        attrs_list = node.get('attributes', [])
        attrs = dict(zip(attrs_list[::2], attrs_list[1::2]))
        bid_map[bid] = {
            'tag': node.get('nodeName', '').lower(),
            'attrs': attrs,
        }
    for child in node.get('children', []):
        _walk(child, bid_map)
    for sub in node.get('shadowRoots', []):
        _walk(sub, bid_map)
    cd = node.get('contentDocument')
    if cd:
        _walk(cd, bid_map)


# ── 置信度标记（用于渲染时给 AI 视觉提示）──
CONFIDENCE_MARKER = {
    'high': '',       # 高置信度：不加标记
    'medium': '⚡ ',  # 中：一个闪电
    'low': '? ',      # 低：问号
}


def format_clickable_record(rec: dict, ref_id: int, verbose: bool = False) -> str:
    """把一条 ClickableRecord 渲染成一行文本。

    默认紧凑格式（给 AI / 用户看，强调有用信息）：
      [5] button "Sign in" @top-right → #signin
      [8] ⚡ div "User profile" @top-left (icon) → .user-menu-btn
      [9] ⚡ svg @top-right (icon) → xpath://header//svg[3]
      [12] ? span "see more" @bottom → css:.footer > span:nth-child(3)

    verbose=True 时追加 reason + 尺寸，用于调试：
      [5] button "Sign in" @top-right (button, 80x32) → #signin
    """
    marker = CONFIDENCE_MARKER.get(rec.get('confidence'), '')
    tag = rec.get('tag', '')
    label = (rec.get('label') or rec.get('text') or '').strip()
    zone = rec.get('zone') or ''
    icon_only = bool(rec.get('iconOnly'))
    reason = rec.get('reason') or ''
    rect = rec.get('rect') or {}
    loc = rec.get('locator') or ''

    parts = [f'[{ref_id}]', f'{marker}{tag}']
    if label:
        display = label[:80] + '…' if len(label) > 80 else label
        parts.append(f'"{display}"')
    elif icon_only:
        # 无 label 但有 icon：标注 (icon) 提示这是图标按钮
        parts.append('(icon)')
    if zone:
        parts.append(f'@{zone}')
    if verbose:
        meta_bits = [reason]
        if rect.get('w'):
            meta_bits.append(f'{rect["w"]}x{rect["h"]}')
        parts.append(f'({", ".join(meta_bits)})')
    if loc:
        parts.append(f'→ {loc}')
    return ' '.join(parts)
