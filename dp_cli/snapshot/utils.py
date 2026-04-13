# -*- coding:utf-8 -*-
"""共享工具函数"""
import re

_NOISE_TAGS = {
    'script', 'style', 'noscript', 'meta', 'link',
    'svg', 'path', 'defs', 'symbol', 'use', 'g',
    'iframe', 'template', 'canvas', 'video', 'audio',
    'header', 'footer', 'nav', 'aside'
}

_CONTAINER_TAGS = {
    'html', 'body', 'div', 'section', 'article', 'main',
    'aside', 'nav', 'header', 'footer', 'ul', 'ol', 'dl',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'form', 'fieldset'
}


def is_noise_text(text: str) -> bool:
    """判断是否是无意义的噪音文本（纯空白、纯数字符号、超短无语义）"""
    t = text.strip()
    if not t or len(t) < 2:
        return True
    if re.match(r'^[\s\W]+$', t):
        return True
    return False


def filter_attrs(attrs: dict) -> dict:
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


def suggest_locator(tag: str, attrs: dict, text: str) -> str:
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
