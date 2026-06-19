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


def suggest_locator(tag: str, attrs: dict, text: str, attr_priority: list = None) -> str:
    """为静态元素生成最优 DrissionPage 定位字符串

    :param attr_priority: 自定义属性优先级列表，如 ['data-testid', 'data-test-id', 'id']
                          如果为 None，使用默认优先级
    """
    # 默认属性优先级（按测试最佳实践：测试专用属性 > id > 语义属性 > 样式属性）
    default_priority = [
        'data-testid',      # 测试专用，最稳定
        'data-test',        # data-testid 变种
        'data-test-id',     # data-testid 变种
        'data-qa',          # QA 专用
        'data-cy',          # Cypress 测试框架
        'id',               # 唯一性强，但可能因重构改变
        'aria-label',       # 可访问性属性
        'name',             # 表单元素属性
        'placeholder',      # 占位符
    ]
    # 如果用户指定了自定义优先级，使用用户指定的优先级
    # 如果自定义优先级中没有匹配，回退到默认优先级
    if attr_priority:
        priority = attr_priority + [p for p in default_priority if p not in attr_priority]
    else:
        priority = default_priority

    # 按优先级检查属性
    for attr in priority:
        if attr in attrs and attrs[attr]:
            val = attrs[attr]
            if attr == 'id':
                return f'#{val}'
            else:
                return f'@{attr}={val}'

    # 回退到 class
    cls = attrs.get('class', '')
    if cls:
        classes = [c for c in cls.strip().split() if _is_meaningful_class(c)]
        if classes:
            return f'.{classes[0]}'

    # 回退到 text
    if text and len(text) <= 30:
        return f'text:{text}'

    # 最后回退到 tag
    return f't:{tag}'
