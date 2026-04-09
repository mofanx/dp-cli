# -*- coding:utf-8 -*-
"""
dp-cli snapshot 模块
基于 lxml s_ele() 生成页面结构快照，比 a11y tree 信息更丰富、更高效。
"""
import re

# 可交互 tag 集合
_INTERACTIVE_TAGS = {
    'a', 'button', 'input', 'select', 'textarea', 'label',
    'form', 'details', 'summary', 'option',
}

# 无意义噪音 tag（快照时跳过）
_NOISE_TAGS = {
    'script', 'style', 'noscript', 'head', 'meta', 'link',
    'svg', 'path', 'defs', 'symbol', 'use', 'g', 'br', 'hr',
    'iframe', 'template',
}

# 语义内容 tag（content 模式重点保留）
_CONTENT_TAGS = {
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'p', 'li', 'td', 'th', 'dt', 'dd',
    'span', 'em', 'strong', 'time', 'cite',
    'a', 'button', 'input', 'select', 'textarea',
    'label', 'article', 'section', 'header', 'footer',
    'nav', 'aside', 'main',
}

# full 模式跳过递归的大容器 tag（只保留自身不展开子树）
_CONTAINER_TAGS = set()


def take_snapshot(page, mode: str = 'interactive',
                  selector: str = None, max_depth: int = 8,
                  min_text: int = 2, max_text: int = 500) -> dict:
    """
    生成页面快照。

    :param page: ChromiumPage 或 ChromiumTab
    :param mode: 快照模式
        - 'auto':        自动检测页面类型并输出最有用的信息（推荐，无需手动选模式）
        - 'interactive': 只列出可交互元素（表单页、登录页首选）
        - 'content':     语义区块聚合，识别重复卡片结构并给出 extract 工作流提示
        - 'full':        完整 DOM 树（结构化文本）
        - 'text':        纯文本内容
    :param selector: 限定范围的定位器，None 表示整页
    :param max_depth: full/content 模式最大深度
    :return: 快照数据字典
    """
    page.wait.doc_loaded()

    page_info = {
        'url': page.url,
        'title': page.title,
    }

    if selector:
        root = page.s_ele(selector)
        if not root or root.__class__.__name__ == 'NoneElement':
            return {'page': page_info, 'error': f'Selector not found: {selector}'}
    else:
        root = page.s_ele()

    if mode == 'auto':
        return _auto_snapshot(page, root, page_info, max_depth, min_text, max_text)
    elif mode == 'interactive':
        elements = _extract_interactive(page, selector)
        return {
            'page': page_info,
            'mode': 'interactive',
            'count': len(elements),
            'elements': elements,
        }
    elif mode == 'content':
        result = _extract_content_blocks(root, max_depth=max_depth,
                                         min_text=min_text, max_text=max_text)
        return {
            'page': page_info,
            'mode': 'content',
            **result,
        }
    elif mode == 'full':
        tree = _build_tree(root, depth=0, max_depth=max_depth)
        return {
            'page': page_info,
            'mode': 'full',
            'tree': tree,
        }
    elif mode == 'text':
        text = root.text if root else ''
        return {
            'page': page_info,
            'mode': 'text',
            'text': text,
        }
    else:
        return {'error': f'Unknown mode: {mode}'}


def _extract_controls(page) -> list:
    """
    提取表单控件（input/select/textarea/button），不包括链接。
    专用于 auto 模式的表单页输出。
    """
    elements = []
    idx = 0
    s_eles = page.s_eles(
        'xpath://*[self::button or self::input or self::select '
        'or self::textarea or self::label]'
    )
    for ele in s_eles:
        try:
            tag = ele.tag.lower()
            attrs = ele.attrs
            text = (ele.text or '').strip()[:200]
            is_hidden = attrs.get('type', '').lower() == 'hidden'
            info = {
                'idx': idx,
                'tag': tag,
                'text': text,
                'attrs': _filter_attrs(attrs),
                'loc': _suggest_locator_static(tag, attrs, text),
                'hidden': is_hidden,
            }
            if tag == 'input':
                info['input_type'] = attrs.get('type', 'text').lower()
            elements.append(info)
            idx += 1
        except Exception:
            continue
    return elements


def _extract_interactive(page, selector: str = None) -> list:
    """
    提取所有可交互/有意义的元素，使用 DrissionPage 的 s_ele 批量解析（高效）。
    同时尝试获取可见性状态（通过 ChromiumElement）。
    """
    elements = []
    idx = 0

    # 利用 s_eles 批量获取静态元素（lxml，不走 CDP）
    scope = selector or 'xpath://*'
    s_eles = page.s_eles(scope) if selector else page.s_eles(
        'xpath://*[self::a or self::button or self::input or self::select '
        'or self::textarea or self::label or self::form or self::details '
        'or self::summary]'
    )

    for ele in s_eles:
        try:
            tag = ele.tag.lower()
            attrs = ele.attrs
            text = (ele.text or '').strip()[:200]

            # 过滤 hidden input（有用但标记出来）
            is_hidden = attrs.get('type', '').lower() == 'hidden'

            info = {
                'idx': idx,
                'tag': tag,
                'text': text,
                'attrs': _filter_attrs(attrs),
                'loc': _suggest_locator_static(tag, attrs, text),
                'hidden': is_hidden,
            }

            # input 额外信息
            if tag == 'input':
                info['input_type'] = attrs.get('type', 'text').lower()

            # a 标签附带 href
            if tag == 'a' and attrs.get('href'):
                info['href'] = attrs.get('href')

            elements.append(info)
            idx += 1
        except Exception:
            continue

    return elements


# ── Auto 模式 ─────────────────────────────────────────────────────────────

def _auto_snapshot(page, root, page_info: dict, max_depth: int,
                   min_text: int, max_text: int) -> dict:
    """
    auto 模式：自动检测页面类型并输出最有用的信息。

    检测顺序：列表页 > 内容页 > 表单页
    表单最后判断：很多页面有搜索框但主要内容是列表，不应被表单判断覆盖。
    """
    # 1. 列表页检测（最高优先级）
    cards_info = _detect_card_list(page)
    if cards_info and cards_info['count'] >= 3:
        return {
            'page': page_info,
            'mode': 'auto',
            'detected_type': 'list_page',
            **cards_info,
        }

    # 2. 内容页检测（有大段文字的详情页）
    result = _extract_content_blocks(root, max_depth=max_depth,
                                     min_text=min_text, max_text=max_text)
    if result['count'] >= 8:
        return {
            'page': page_info,
            'mode': 'auto',
            'detected_type': 'content_page',
            **result,
        }

    # 3. 表单页检测（最后判断）
    inputs = page.s_eles('xpath://*[self::input or self::select or self::textarea]')
    visible_inputs = [e for e in inputs
                      if e.attrs.get('type', '') not in ('hidden', 'submit', 'button')]
    if len(visible_inputs) >= 2:
        elements = _extract_controls(page)  # 只输出表单控件，不包括链接
        return {
            'page': page_info,
            'mode': 'auto',
            'detected_type': 'form_page',
            'hint': '检测到表单页。使用 fill/select/click 操作表单元素。',
            'count': len(elements),
            'elements': elements,
        }

    # 4. 默认：输出 content（即使节点少）
    return {
        'page': page_info,
        'mode': 'auto',
        'detected_type': 'content_page',
        **result,
    }


def _detect_card_list(page) -> dict:
    """
    检测页面是否存在重复卡片结构（列表页）。
    两阶段策略：
    1. 静态 lxml（s_eles）快速扫描 HTML 源码中的重复元素
    2. 动态 CDP（eles）处理 JS 动态渲染内容（第一阶段未命中时）
    全程纯内容评分，不依赖类名关键词过滤。
    """
    # ── 第一阶段：静态 lxml 扫描 ─────────────────────────────────
    all_els = page.s_eles(
        'xpath://div[@class] | xpath://article[@class] '
        '| xpath://li[@class] | xpath://section[@class]'
    )
    class_count: dict = {}
    for ele in all_els:
        cls = (ele.attrs.get('class') or '').strip()
        if not cls or len(cls) > 200:
            continue
        first_cls = cls.split()[0]
        if not first_cls or len(first_cls) < 3:
            continue
        class_count[first_cls] = class_count.get(first_cls, 0) + 1

    # 只对高频 class 评分，忽略频次过低的
    static_iter = []
    for first_cls, cnt in sorted(class_count.items(), key=lambda x: -x[1]):
        if cnt < 3:
            break
        try:
            items = page.s_eles(f'css:.{first_cls}')
            if len(items) >= 3:
                static_iter.append((first_cls, cnt, list(items)))
        except Exception:
            pass

    static_candidates = _score_card_candidates(static_iter)
    for score, items, sel, cls in static_candidates:
        if score < 8:
            break
        result = _build_card_result(items, sel, container_class=cls, use_cdp=False)
        if result:
            return result

    # ── 第二阶段： CDP 动态 DOM（应对 JS 动态渲染） ──────────────────
    # 用 JS 计数重复 class，需要走 CDP，技能费时更多但能命中动态内容
    try:
        js_result = page.run_js("""
            const freq = {};
            const els = document.querySelectorAll('[class]');
            for (const el of els) {
                const tag = el.tagName.toLowerCase();
                if (!['div','article','li','section','a'].includes(tag)) continue;
                const first = el.className.trim().split(/\\s+/)[0];
                if (first && first.length >= 3 && first.length <= 60)
                    freq[first] = (freq[first] || 0) + 1;
            }
            return Object.entries(freq)
                .filter(([k,v]) => v >= 3)
                .sort((a,b) => b[1]-a[1])
                .slice(0, 30)
                .map(([k,v]) => ({cls: k, cnt: v}));
        """)
    except Exception:
        js_result = []

    if js_result:
        cdp_iter = []
        for item in (js_result or []):
            try:
                cls = item['cls']
                cnt = item['cnt']
                items = page.eles(f'css:.{cls}')
                if len(items) >= 3:
                    cdp_iter.append((cls, cnt, list(items)))
            except Exception:
                pass

        cdp_candidates = _score_card_candidates(cdp_iter, use_cdp=True)
        for score, items, sel, cls in cdp_candidates:
            if score < 8:
                break
            result = _build_card_result(items, sel, container_class=cls, use_cdp=True)
            if result:
                return result

    return None


def _score_card_candidates(items_iter, use_cdp=False) -> list:
    """
    核心评分函数：统一对静态/动态元素列表进行内容丰富度评分。
    items_iter: list of (first_cls, cnt, items)
    返回: sorted list of (score, items, sel, cls)
    """
    candidates = []
    for first_cls, cnt, items in items_iter:
        if len(items) < 3:
            continue
        sample = list(items)[:12]
        texts = []
        for it in sample:
            try:
                texts.append((it.text or '').strip())
            except Exception:
                texts.append('')

        avg_len = sum(len(t) for t in texts) / max(len(texts), 1)
        multiline_ratio = sum(1 for t in texts if '\n' in t) / max(len(texts), 1)
        unique_ratio = len(set(texts)) / max(len(texts), 1)
        count_weight = min(cnt, 50) / 50.0

        # 子元素层次权重：优先选高层容器而非单字段节点
        try:
            child_count = len(list(sample[0].children()))
        except Exception:
            child_count = 0
        depth_weight = min(child_count / 3, 2.0)

        score = ((avg_len * 0.5 + avg_len * multiline_ratio * 2.0)
                 * unique_ratio * count_weight * max(depth_weight, 0.5))
        candidates.append((score, list(items), f'css:.{first_cls}', first_cls))

    candidates.sort(key=lambda x: -x[0])
    return candidates


def _build_card_result(items, selector: str, container_class: str = None,
                       use_cdp: bool = False) -> dict:
    """从卡片列表中提取前5条内容，并给出 extract 工作流提示"""
    sample_cards = []
    for item in list(items)[:5]:
        card = _extract_card_fields(item, use_cdp=use_cdp)
        if card:
            sample_cards.append(card)

    if not sample_cards:
        return None

    # 质量检查：多张卡片之间需要有足够的字段交集，否则不是真正的列表页
    if len(sample_cards) >= 2:
        non_link_keys = [set(k for k in c if not k.startswith('_')) for c in sample_cards]
        common = non_link_keys[0].copy()
        for s in non_link_keys[1:]:
            common &= s
        # 有效列表页：至少1个公共字段，或者总字段交集比例 > 30%
        total_unique = len(set().union(*non_link_keys))
        if not common and total_unique > 0:
            return None  # 字段完全不同，说明不是结构化列表

    if use_cdp:
        field_hints = _infer_fields_from_sample_cards(sample_cards)
    else:
        field_hints = _infer_field_selectors(list(items)[:3], use_cdp=False)

    container_sel = f'css:.{container_class}' if container_class else selector

    result = {
        'count': len(items),
        'container_selector': container_sel,
        'sample_cards': sample_cards,
        'hint': (
            f'检测到列表页，共 {len(items)} 个卡片（{container_sel}）。\n'
            f'建议使用以下命令批量提取所有数据：\n'
            f'  dp extract "{container_sel}" \'{_format_fields_json(field_hints)}\''
        ),
        'suggested_fields': field_hints,
    }
    return result


def _extract_card_fields(card_ele, use_cdp: bool = False) -> dict:
    """从单张卡片元素提取所有有意义的文本/链接字段。
    use_cdp=True 时使用 CDP eles 查询子元素，并用 JS 提取字段。
    """
    result = {}
    try:
        if use_cdp:
            # CDP 元素用 JS 直接提取所有带 class 的子元素文本
            # 这比递归 children() 更可靠
            try:
                fields_js = card_ele.run_js("""
                    const result = {};
                    const all = this.querySelectorAll('[class]');
                    let idx = 0;
                    for (const el of all) {
                        const tag = el.tagName.toLowerCase();
                        if (['script','style','svg'].includes(tag)) continue;
                        const text = el.innerText ? el.innerText.trim() : '';
                        if (!text || text.length < 2 || text.length > 300) continue;
                        // 只要叶子元素（没有包含文本的子元素）
                        const childTexts = Array.from(el.children)
                            .map(c => c.innerText ? c.innerText.trim() : '');
                        const isLeaf = !childTexts.some(t => t.length > 1);
                        if (!isLeaf) continue;
                        const cls = el.className.trim().split(/\\s+/)[0];
                        const key = cls || 'field_' + idx;
                        if (!result[key]) result[key] = text;
                        idx++;
                        if (idx > 20) break;
                    }
                    return result;
                """)
                if isinstance(fields_js, dict):
                    result.update(fields_js)
            except Exception:
                pass

            # 收集链接
            try:
                links = card_ele.eles('css:a', timeout=0)
                for a in list(links)[:3]:
                    href = (a.attrs.get('href') or '')
                    link_text = (a.text or '').strip()
                    if href and not href.startswith('javascript'):
                        result[f'_link_{link_text[:15] or "link"}'] = href
            except Exception:
                pass
        else:
            # 静态元素用递归 children()
            leaf_texts = _collect_leaf_texts(card_ele, max_items=20)
            for i, (text, tag, cls) in enumerate(leaf_texts):
                key = cls or f'field_{i}'
                if key in result:
                    key = f'{key}_{i}'
                result[key] = text
            try:
                links = card_ele.s_eles('css:a')
                for a in list(links)[:3]:
                    href = (a.attrs.get('href') or '')
                    link_text = (a.text or '').strip()
                    if href and not href.startswith('javascript'):
                        result[f'_link_{link_text[:15] or "link"}'] = href
            except Exception:
                pass
    except Exception:
        pass
    return result


def _collect_leaf_texts(ele, depth: int = 0, max_depth: int = 8,
                        max_items: int = 20) -> list:
    """递归收集元素内所有有文字的叶子节点，返回 (text, tag, first_class) 列表"""
    results = []
    if depth > max_depth or len(results) >= max_items:
        return results

    try:
        tag = (ele.tag or '').lower()
    except Exception:
        return results

    if tag in _NOISE_TAGS:
        return results

    try:
        attrs = ele.attrs
    except Exception:
        attrs = {}

    try:
        children = list(ele.children())
    except Exception:
        children = []

    # 叶子节点或没有有意义子节点时取自身文本
    if not children or tag in ('span', 'a', 'em', 'strong', 'b', 'i', 'time',
                                'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p',
                                'td', 'th', 'li', 'dt', 'dd', 'label',
                                'button'):
        try:
            text = (ele.text or '').strip()
        except Exception:
            text = ''
        if text and 2 <= len(text) <= 300 and not _is_noise_text(text):
            cls = (attrs.get('class') or '').split()[0] if attrs.get('class') else ''
            results.append((text, tag, cls))
        return results

    # 容器节点递归
    for child in children:
        results.extend(_collect_leaf_texts(child, depth + 1, max_depth, max_items))
        if len(results) >= max_items:
            break
    return results


def _infer_field_selectors(items: list, use_cdp: bool = False) -> dict:
    """
    分析前几张卡片，推断各字段的最优 CSS 选择器。
    use_cdp=True 时使用 CDP eles 查询，适合动态内容。
    """
    if not items:
        return {}

    all_classes = []
    for item in items:
        card_classes = _get_leaf_classes(item)
        all_classes.append(card_classes)

    if not all_classes:
        return {}

    common = set(all_classes[0])
    for s in all_classes[1:]:
        common &= set(s)

    fields = {}
    first_item = items[0]
    for cls in list(common)[:8]:
        try:
            if use_cdp:
                sample_ele = first_item.ele(f'css:.{cls}', timeout=0)
            else:
                sample_ele = first_item.s_ele(f'css:.{cls}')
            if sample_ele and sample_ele.__class__.__name__ != 'NoneElement':
                sample_text = (sample_ele.text or '').strip()[:60]
                if sample_text and not _is_noise_text(sample_text):
                    fields[cls] = f'css:.{cls}'
        except Exception:
            pass

    return fields


def _get_leaf_classes(ele, depth: int = 0, max_depth: int = 6) -> list:
    """收集元素下所有叶子节点的第一个 class 名。
    兼容静态(s_ele)和动态(ele/CDP)元素。
    """
    results = []
    if depth > max_depth:
        return results
    try:
        tag = (ele.tag or '').lower()
        if tag in _NOISE_TAGS:
            return results
        attrs = ele.attrs
        cls = (attrs.get('class') or '').split()
        first_cls = cls[0] if cls else ''

        # 尝试获取子元素（静态元素用 children()，动态元素可能没有这个方法）
        children = []
        if hasattr(ele, 'children'):
            try:
                children = list(ele.children())
            except Exception:
                pass

        is_leaf = not children or tag in ('span', 'a', 'em', 'strong', 'b', 'i',
                                           'time', 'p', 'td', 'th', 'li', 'button')
        if is_leaf and first_cls and len(first_cls) >= 3:
            text = (ele.text or '').strip()
            if text and 2 <= len(text) <= 200:
                results.append(first_cls)
        elif children:
            for child in children:
                results.extend(_get_leaf_classes(child, depth + 1, max_depth))
        else:
            # CDP 元素没有 children() 方法，尝试用 s_eles 获取子元素
            try:
                sub_eles = ele.eles('xpath://*[@class]', timeout=0)
                for sub in list(sub_eles)[:20]:
                    sub_cls = (sub.attrs.get('class') or '').split()
                    sub_first = sub_cls[0] if sub_cls else ''
                    if sub_first and len(sub_first) >= 3:
                        sub_text = (sub.text or '').strip()
                        if sub_text and 2 <= len(sub_text) <= 200:
                            results.append(sub_first)
            except Exception:
                pass
    except Exception:
        pass
    return results


def _infer_fields_from_sample_cards(sample_cards: list) -> dict:
    """
    CDP 模式专用：从 sample_cards 的 key（已是 class 名）直接生成字段选择器。
    取所有卡片都有的字段（交集），过滤链接字段（以 _link_ 开头）。
    """
    if not sample_cards:
        return {}
    # 取非链接字段的交集
    common_keys = set(k for k in sample_cards[0] if not k.startswith('_'))
    for card in sample_cards[1:]:
        common_keys &= set(k for k in card if not k.startswith('_'))
    # class 名即选择器
    return {k: f'css:.{k}' for k in list(common_keys)[:8]
            if len(k) >= 3 and not k.startswith('field_')}


def _format_fields_json(fields: dict) -> str:
    """将字段字典格式化为 extract 命令的 JSON 字符串"""
    import json
    simple = {name: sel for name, sel in fields.items()}
    return json.dumps(simple, ensure_ascii=False)


# ── Content 模式（重新设计）─────────────────────────────────────────────────

def _extract_content_blocks(root, max_depth: int = 8,
                            min_text: int = 2, max_text: int = 500) -> dict:
    """
    content 模式（重新设计版）：
    - 不再在第一个有文本节点处截止，而是完整遍历 DOM
    - 区分「文本叶子」和「区块容器」
    - 叶子节点：直接输出 tag/text/loc/class
    - 区块容器（div/section/article/ul/ol）：输出区块标识 + 其下所有叶子文本
    - 自动跳过噪音（script/style/空容器/超短文本）
    返回 { nodes: [...], hint: '...' }
    """
    nodes = []
    seen_texts = set()  # 去重，避免父子节点重复输出同一段文本

    def _walk(ele, depth, in_block=False):
        if depth > max_depth:
            return
        try:
            tag = (ele.tag or '').lower()
        except Exception:
            return
        if tag in _NOISE_TAGS:
            return

        try:
            attrs = ele.attrs
        except Exception:
            attrs = {}

        # 过滤内嵌 CSS/数据
        if tag == 'textarea':
            t = attrs.get('type', '')
            if 'css' in t or 'json' in t:
                return

        try:
            children = list(ele.children())
        except Exception:
            children = []

        # 当前节点的直接文本
        try:
            raw_text = (ele.text or '').strip()
        except Exception:
            raw_text = ''

        is_leaf = (not children
                   or tag in ('span', 'a', 'em', 'strong', 'b', 'i', 'time',
                              'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                              'p', 'td', 'th', 'li', 'dt', 'dd',
                              'label', 'button', 'option'))

        if is_leaf:
            text = raw_text
            if (text and min_text <= len(text) <= max_text
                    and not _is_noise_text(text)
                    and text not in seen_texts):
                seen_texts.add(text)
                first_cls = (attrs.get('class') or '').split()[0] if attrs.get('class') else ''
                node = {
                    'tag': tag,
                    'text': text[:300],
                    'loc': _suggest_locator_static(tag, attrs, text),
                }
                if first_cls:
                    node['class'] = first_cls
                nodes.append(node)
            return

        # 容器节点：检查是否是有意义的区块
        cls_str = (attrs.get('class') or '').strip()
        ele_id = attrs.get('id', '')
        is_semantic = (tag in ('article', 'section', 'header', 'footer',
                               'nav', 'aside', 'main', 'ul', 'ol', 'table',
                               'form', 'fieldset', 'dl')
                       or bool(ele_id))

        # 输出区块分隔标记（只对有语义意义的容器）
        if is_semantic and depth <= 6:
            block_label = ele_id or cls_str.split()[0] if cls_str else tag
            if block_label and block_label != tag:
                nodes.append({
                    'tag': tag,
                    'text': f'[{tag}#{ele_id or block_label}]',
                    'loc': f'#{ele_id}' if ele_id else f'css:.{cls_str.split()[0]}',
                    'class': cls_str.split()[0] if cls_str else '',
                    '_is_block': True,
                })

        for child in children:
            _walk(child, depth + 1, in_block or is_semantic)

    _walk(root, 0)

    # 过滤掉只有 _is_block 且后面没有内容的孤立分隔符
    cleaned = []
    for i, n in enumerate(nodes):
        if n.get('_is_block'):
            # 只保留后面紧跟有内容节点的区块标记
            has_content_after = any(
                not nodes[j].get('_is_block')
                for j in range(i + 1, min(i + 4, len(nodes)))
            )
            if has_content_after:
                del n['_is_block']
                cleaned.append(n)
        else:
            cleaned.append(n)

    hint = ''
    if len(cleaned) < 5:
        hint = ('内容节点较少，页面可能需要登录或为动态加载。'
                '建议先 dp wait --loaded，或尝试 dp snapshot --mode text 查看原始文本。')

    return {
        'count': len(cleaned),
        'nodes': cleaned,
        'hint': hint,
    }


def _extract_content_nodes(root, max_depth: int = 6,
                           min_text: int = 2, max_text: int = 500) -> list:
    """兼容旧调用，内部调用新版 _extract_content_blocks"""
    return _extract_content_blocks(root, max_depth, min_text, max_text)['nodes']


def _is_noise_text(text: str) -> bool:
    """判断是否是无意义的噪音文本（纯空白、纯数字符号、超短无语义）"""
    t = text.strip()
    if not t or len(t) < 2:
        return True
    # 全是标点/符号
    if re.match(r'^[\s\W]+$', t):
        return True
    return False


def extract_structured(page, container: str, fields: dict,
                       limit: int = 100) -> list:
    """
    结构化批量提取——核心数据提取原语。

    在页面上找到所有 container 匹配的元素（每个视为一条记录），
    然后在每个容器内按 fields 字典提取各字段值。

    :param page: ChromiumPage
    :param container: 容器定位器，如 'css:.job-card' 或 'xpath://li[@class="item"]'
    :param fields: 字段映射字典，如
        {
          "title":  "css:.job-name",
          "salary": "css:.salary",
          "company": "css:.company-name",
          "tags":   {"selector": "css:.tag", "multi": True},
          "url":    {"selector": "css:a", "attr": "href"},
        }
        值可以是：
        - 字符串：子元素定位器，取 text
        - dict with 'selector': 子元素定位器
          - 'multi': True → 返回文本列表
          - 'attr': 'href' → 取属性值而非 text
          - 'default': 缺失时的默认值
    :param limit: 最多提取多少条记录
    :return: list of dict
    """
    containers = page.s_eles(container)
    if not containers:
        return []

    results = []
    for item in list(containers)[:limit]:
        record = {}
        for field_name, spec in fields.items():
            # 规范化 spec
            if isinstance(spec, str):
                spec = {'selector': spec}

            sel = spec.get('selector', '')
            multi = spec.get('multi', False)
            attr = spec.get('attr', None)
            default = spec.get('default', '')

            try:
                if multi:
                    eles = item.s_eles(sel)
                    record[field_name] = [
                        (e.attr(attr) if attr else (e.text or '').strip())
                        for e in eles
                    ]
                else:
                    ele = item.s_ele(sel)
                    if ele and ele.__class__.__name__ != 'NoneElement':
                        if attr:
                            record[field_name] = ele.attr(attr) or default
                        else:
                            record[field_name] = (ele.text or '').strip() or default
                    else:
                        record[field_name] = default
            except Exception:
                record[field_name] = default

        results.append(record)

    return results


def query_elements(page, selector: str, fields: list,
                   limit: int = 200) -> list:
    """
    query 模式：找到所有匹配 selector 的元素，批量提取指定属性/文本。

    :param page: ChromiumPage
    :param selector: 元素定位器
    :param fields: 要提取的字段列表，如 ['text', 'href', 'id', 'class']
                   'text' → 文本内容
                   其他 → HTML 属性值
    :param limit: 最多返回多少条
    :return: list of dict
    """
    eles = page.s_eles(selector)
    results = []
    for ele in list(eles)[:limit]:
        record = {}
        for f in fields:
            try:
                if f == 'text':
                    record['text'] = (ele.text or '').strip()
                elif f == 'tag':
                    record['tag'] = ele.tag
                elif f == 'loc':
                    record['loc'] = _suggest_locator_static(
                        ele.tag, ele.attrs, (ele.text or '').strip()
                    )
                else:
                    record[f] = ele.attr(f) or ''
            except Exception:
                record[f] = ''
        results.append(record)
    return results


def _build_tree(ele, depth: int, max_depth: int) -> dict:
    """递归构建 DOM 树（s_ele 静态解析，高效）"""
    if ele is None:
        return {}
    try:
        tag = ele.tag.lower()
    except Exception:
        return {}

    node = {
        'tag': tag,
        'attrs': _filter_attrs(ele.attrs),
        'text': (ele.raw_text or '').strip()[:100],
    }

    if depth < max_depth and tag not in _CONTAINER_TAGS:
        children = []
        try:
            for child in ele.children():
                child_node = _build_tree(child, depth + 1, max_depth)
                if child_node:
                    children.append(child_node)
        except Exception:
            pass
        if children:
            node['children'] = children

    return node


def _filter_attrs(attrs: dict) -> dict:
    """过滤掉过长或无意义的属性"""
    result = {}
    skip = {'style', 'srcset', 'sizes', 'integrity', 'crossorigin'}
    for k, v in attrs.items():
        if k in skip:
            continue
        if isinstance(v, str) and len(v) > 200:
            v = v[:200] + '...'
        result[k] = v
    return result


def _suggest_locator_static(tag: str, attrs: dict, text: str) -> str:
    """为静态元素生成最优 DrissionPage 定位字符串"""
    if attrs.get('id'):
        return f'#{attrs["id"]}'

    for semantic in ('data-testid', 'data-qa', 'data-cy', 'aria-label', 'name', 'placeholder'):
        if attrs.get(semantic):
            val = attrs[semantic]
            return f'@{semantic}={val}'

    cls = attrs.get('class', '')
    if cls:
        classes = [c for c in cls.strip().split() if not re.match(r'^[a-z]+-\w{4,}$', c)]
        if classes:
            return f'.{classes[0]}'

    if text and len(text) <= 30:
        return f'text:{text}'

    return f't:{tag}'


def render_snapshot_text(snapshot: dict) -> str:
    """
    将快照数据渲染为人类/AI 可读的文本格式。
    """
    lines = []
    page_info = snapshot.get('page', {})
    lines.append(f"### Page")
    lines.append(f"- URL: {page_info.get('url', '')}")
    lines.append(f"- Title: {page_info.get('title', '')}")
    lines.append('')

    mode = snapshot.get('mode', 'interactive')
    detected_type = snapshot.get('detected_type', '')

    # hint 优先显示
    hint = snapshot.get('hint', '')
    if hint:
        lines.append(f"### Hint")
        for h_line in hint.split('\n'):
            lines.append(h_line)
        lines.append('')

    if mode in ('interactive', 'auto') and 'elements' in snapshot:
        label = 'Interactive Elements'
        if detected_type == 'form_page':
            label = 'Form Elements'
        lines.append(f"### {label} ({snapshot.get('count', 0)} found)")
        lines.append('')
        for ele in snapshot.get('elements', []):
            idx = ele['idx']
            tag = ele['tag']
            text = ele.get('text', '')
            loc = ele.get('loc', '')
            attrs = ele.get('attrs', {})
            hidden = ele.get('hidden', False)

            attr_parts = []
            for k in ('type', 'name', 'placeholder', 'href', 'value', 'aria-label', 'id', 'class'):
                if k in attrs:
                    v = attrs[k]
                    if len(str(v)) <= 50:
                        attr_parts.append(f'{k}="{v}"')

            attr_str = ' '.join(attr_parts)
            text_str = f' "{text}"' if text else ''
            hidden_str = ' [hidden]' if hidden else ''

            lines.append(f"[{idx}] <{tag}{(' ' + attr_str) if attr_str else ''}>{text_str}{hidden_str}")
            lines.append(f"     loc: {loc}")

    elif mode in ('auto',) and 'sample_cards' in snapshot:
        # 列表页输出
        lines.append(f"### Detected: List Page")
        lines.append(f"- Container: {snapshot.get('container_selector', '')}")
        lines.append(f"- Total cards: {snapshot.get('count', 0)}")
        lines.append('')
        lines.append("#### Sample Cards (first 5)")
        lines.append('')
        for i, card in enumerate(snapshot.get('sample_cards', [])):
            lines.append(f"--- Card {i+1} ---")
            for k, v in card.items():
                if not k.startswith('_'):
                    lines.append(f"  {k}: {str(v)[:80]}")
                else:
                    lines.append(f"  (link) {k[6:]}: {str(v)[:80]}")
        lines.append('')
        lines.append("#### Suggested Fields for extract")
        for fname, fsel in snapshot.get('suggested_fields', {}).items():
            first_card = snapshot.get('sample_cards', [{}])[0]
            sample_val = first_card.get(fname, '')
            lines.append(f"  {fname:20s} → {fsel}   # e.g. {str(sample_val)[:40]}")

    elif mode in ('content', 'auto') and 'nodes' in snapshot:
        nodes = snapshot.get('nodes', [])
        lines.append(f"### Content Nodes ({len(nodes)} found)")
        lines.append('')
        for node in nodes:
            tag = node.get('tag', '')
            text = node.get('text', '')
            loc = node.get('loc', '')
            cls = node.get('class', '')
            if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                prefix = '#' * int(tag[1])
                lines.append(f"{prefix} {text}")
            elif text.startswith('[') and text.endswith(']'):
                lines.append(f"\n──── {text} ────")
            else:
                cls_hint = f"  .{cls}" if cls else ''
                lines.append(f"- {text}{cls_hint}")
            if loc and loc not in (f't:{tag}', ''):
                lines.append(f"  → loc: {loc}")

    elif mode == 'full':
        lines.append("### DOM Tree")
        lines.append('')
        _render_tree_lines(snapshot.get('tree', {}), lines, indent=0)

    elif mode == 'text':
        lines.append("### Page Text")
        lines.append('')
        lines.append(snapshot.get('text', ''))

    if 'error' in snapshot:
        lines.append(f"\n### Error")
        lines.append(snapshot['error'])

    return '\n'.join(lines)


def _render_tree_lines(node: dict, lines: list, indent: int) -> None:
    if not node:
        return
    tag = node.get('tag', '')
    attrs = node.get('attrs', {})
    text = node.get('text', '').strip()
    children = node.get('children', [])

    attr_parts = []
    for k in ('id', 'class', 'type', 'name', 'href', 'placeholder'):
        if k in attrs:
            attr_parts.append(f'{k}="{attrs[k]}"')

    attr_str = (' ' + ' '.join(attr_parts)) if attr_parts else ''
    text_str = f' "{text[:50]}"' if text else ''
    prefix = '  ' * indent

    lines.append(f"{prefix}<{tag}{attr_str}>{text_str}")
    for child in children:
        _render_tree_lines(child, lines, indent + 1)
