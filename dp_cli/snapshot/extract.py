# -*- coding:utf-8 -*-
"""
extract_structured / query_elements — 数据提取函数
"""
from .utils import suggest_locator

_JS_CSS_PATH = """
var el = this;
var parts = [];
while (el && el !== document.body && el.nodeType === 1) {
    var seg = el.tagName.toLowerCase();
    if (el.id && /^[a-zA-Z][\\w-]*$/.test(el.id)) {
        parts.unshift('#' + el.id);
        break;
    }
    var classes = Array.from(el.classList)
        .filter(function(c) { return c.length >= 3; });
    if (classes.length > 0) {
        seg = '.' + classes[0];
        var siblings = el.parentElement
            ? Array.from(el.parentElement.querySelectorAll(':scope > ' + seg))
            : [];
        if (siblings.length > 1) {
            var idx = siblings.indexOf(el) + 1;
            seg = seg + ':nth-child(' + idx + ')';
        }
    } else {
        var allSiblings = el.parentElement
            ? Array.from(el.parentElement.children).filter(function(c) { return c.tagName === el.tagName; })
            : [];
        if (allSiblings.length > 1) {
            var idx2 = Array.from(el.parentElement.children).indexOf(el) + 1;
            seg = seg + ':nth-child(' + idx2 + ')';
        }
    }
    parts.unshift(seg);
    el = el.parentElement;
}
return parts.join(' > ');
"""

_JS_XPATH = """
var el = this;
var parts = [];
while (el && el.nodeType === 1) {
    var seg = el.tagName.toLowerCase();
    if (el.id && /^[a-zA-Z][\\w-]*$/.test(el.id)) {
        parts.unshift('//' + seg + '[@id="' + el.id + '"]');
        return parts.join('/');
    }
    var siblings = el.parentElement
        ? Array.from(el.parentElement.children).filter(function(c) { return c.tagName === el.tagName; })
        : [];
    if (siblings.length > 1) {
        var idx = siblings.indexOf(el) + 1;
        seg = seg + '[' + idx + ']';
    }
    parts.unshift(seg);
    el = el.parentElement;
}
return '/' + parts.join('/');
"""


def extract_structured(page, container: str, fields: dict,
                       limit: int = 100) -> list:
    """
    结构化批量提取。

    :param container: 容器定位器，如 'css:.job-card'
    :param fields: 字段映射字典
    :param limit: 最多提取多少条
    """
    containers = page.s_eles(container)
    if not containers:
        return []

    results = []
    for item in list(containers)[:limit]:
        record = {}
        for field_name, spec in fields.items():
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
                        (e.attr(attr) if attr else (e.raw_text or '').strip())
                        for e in eles
                    ]
                else:
                    ele = item.s_ele(sel)
                    if ele and ele.__class__.__name__ != 'NoneElement':
                        if attr:
                            record[field_name] = ele.attr(attr) or default
                        else:
                            record[field_name] = (ele.raw_text or '').strip() or default
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
    """
    try:
        eles = page.eles(selector, timeout=5)
    except Exception:
        eles = page.s_eles(selector)

    results = []
    for ele in list(eles)[:limit]:
        record = {}
        for f in fields:
            try:
                if f == 'text':
                    record['text'] = (ele.raw_text or '').strip()
                elif f == 'tag':
                    record['tag'] = ele.tag
                elif f == 'loc':
                    record['loc'] = suggest_locator(
                        ele.tag, ele.attrs, (ele.raw_text or '').strip()[:50]
                    )
                elif f == 'css_path':
                    try:
                        path = ele.run_js(_JS_CSS_PATH)
                        record['css_path'] = f'css:{path}' if path else ''
                    except Exception:
                        record['css_path'] = ''
                elif f == 'xpath':
                    try:
                        path = ele.run_js(_JS_XPATH)
                        record['xpath'] = f'xpath:{path}' if path else ''
                    except Exception:
                        record['xpath'] = ''
                else:
                    val = ele.attrs.get(f, '') if hasattr(ele, 'attrs') else ''
                    record[f] = val or ''
            except Exception:
                record[f] = ''
        results.append(record)
    return results
