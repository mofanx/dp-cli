# -*- coding:utf-8 -*-
"""共享工具函数"""
import re


def _is_meaningful_class(cls: str) -> bool:
    """判断 CSS 类名是否有语义（过滤混淆/哈希类名）"""
    if not cls or len(cls) < 2:
        return False
    # CSS module 风格：prefix-hash，后缀含数字（如 btn-abc1234、css-1d2e3f）
    if re.match(r'^[a-z]+-(?=\w*\d)\w{4,}$', cls):
        return False
    # 纯随机字符串：6+ 字符且无分隔符（-_），大小写混杂或全小写无元音
    if len(cls) >= 6 and not re.search(r'[-_]', cls):
        # 大小写混杂无分隔符（如 hkJMPzDNh、BAyykwGBSi）
        if re.search(r'[a-z]', cls) and re.search(r'[A-Z]', cls):
            return False
        # 全小写但无元音（如 bcdfgh）→ 大概率是哈希
        if cls.islower() and not re.search(r'[aeiou]', cls):
            return False
    return True


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
        classes = [c for c in cls.strip().split() if _is_meaningful_class(c)]
        if classes:
            return f'.{classes[0]}'

    if text and len(text) <= 30:
        return f'text:{text}'

    return f't:{tag}'
