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


def take_snapshot(page, mode: str = 'interactive',
                  selector: str = None, max_depth: int = 8,
                  min_text: int = 2, max_text: int = 500) -> dict:
    """
    生成页面快照。

    :param page: ChromiumPage 或 ChromiumTab
    :param mode: 快照模式
        - 'interactive': 只列出可交互元素（默认，最适合 AI 操控）
        - 'content': 去噪内容树，自动识别有意义文本节点（适合数据提取）
        - 'full': 完整 DOM 树（结构化文本）
        - 'text': 纯文本内容
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

    if mode == 'interactive':
        elements = _extract_interactive(page, selector)
        return {
            'page': page_info,
            'mode': 'interactive',
            'count': len(elements),
            'elements': elements,
        }
    elif mode == 'content':
        nodes = _extract_content_nodes(root, max_depth=max_depth,
                                       min_text=min_text, max_text=max_text)
        return {
            'page': page_info,
            'mode': 'content',
            'count': len(nodes),
            'nodes': nodes,
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


def _extract_content_nodes(root, max_depth: int = 6,
                           min_text: int = 2, max_text: int = 500) -> list:
    """
    content 模式：递归遍历 DOM，只保留有实际文本内容的语义节点。
    自动跳过 script/style 等噪音，返回扁平化的节点列表。
    每个节点包含 tag、text、loc（定位器），便于后续精确定位。
    """
    nodes = []

    def _walk(ele, depth):
        if depth > max_depth:
            return
        try:
            tag = ele.tag.lower() if hasattr(ele, 'tag') else ''
        except Exception:
            return

        if tag in _NOISE_TAGS:
            return

        try:
            text = (ele.text or '').strip()
        except Exception:
            text = ''

        try:
            attrs = ele.attrs
        except Exception:
            attrs = {}

        # 过滤内嵌 CSS/数据的 textarea（type=text/css 或文本超长）
        if tag == 'textarea':
            type_attr = attrs.get('type', '')
            if 'css' in type_attr or 'json' in type_attr:
                return
            if len(text) > 200:
                return

        # 判断是否为有意义的叶子节点
        is_content = (
            tag in _CONTENT_TAGS
            and text
            and min_text <= len(text) <= max_text
            and not _is_noise_text(text)
        )

        if is_content:
            nodes.append({
                'tag': tag,
                'text': text[:300],
                'loc': _suggest_locator_static(tag, attrs, text),
                'attrs': {k: v for k, v in attrs.items()
                          if k in ('id', 'class', 'href', 'data-jobid', 'data-lid')
                          and v and len(str(v)) < 100},
            })
            # 叶子节点不再深入
            return

        # 容器节点继续递归
        try:
            for child in ele.children():
                _walk(child, depth + 1)
        except Exception:
            pass

    _walk(root, 0)
    return nodes


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
    将快照数据渲染为人类/AI 可读的文本格式（类似 playwright-cli 输出风格）。
    """
    lines = []
    page = snapshot.get('page', {})
    lines.append(f"### Page")
    lines.append(f"- URL: {page.get('url', '')}")
    lines.append(f"- Title: {page.get('title', '')}")
    lines.append('')

    mode = snapshot.get('mode', 'interactive')

    if mode == 'interactive':
        lines.append(f"### Interactive Elements ({snapshot.get('count', 0)} found)")
        lines.append('')
        for ele in snapshot.get('elements', []):
            idx = ele['idx']
            tag = ele['tag']
            text = ele.get('text', '')
            loc = ele.get('loc', '')
            attrs = ele.get('attrs', {})
            hidden = ele.get('hidden', False)

            # 构建属性摘要
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

    elif mode == 'full':
        lines.append("### DOM Tree")
        lines.append('')
        _render_tree_lines(snapshot.get('tree', {}), lines, indent=0)

    elif mode == 'content':
        nodes = snapshot.get('nodes', [])
        lines.append(f"### Content Nodes ({len(nodes)} found)")
        lines.append('')
        for node in nodes:
            tag = node.get('tag', '')
            text = node.get('text', '')
            loc = node.get('loc', '')
            prefix = '#' * int(tag[1]) if tag in ('h1','h2','h3','h4','h5','h6') else '-'
            lines.append(f"{prefix} [{tag}] {text}")
            if loc and loc != f't:{tag}':
                lines.append(f"  loc: {loc}")

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
