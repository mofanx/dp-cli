# -*- coding:utf-8 -*-
"""
dp-cli 输出格式化模块
统一的 JSON 输出格式，便于 AI 工具解析。
"""
import json
import sys
from typing import Any, Optional


def ok(data: Any = None, msg: str = None) -> None:
    """成功输出"""
    result = {'status': 'ok'}
    if msg:
        result['message'] = msg
    if data is not None:
        result['data'] = data
    _print(result)


def error(msg: str, code: str = 'ERROR', detail: str = None) -> None:
    """错误输出"""
    result = {'status': 'error', 'code': code, 'message': msg}
    if detail:
        result['detail'] = detail
    _print(result)
    sys.exit(1)


def _print(result: dict) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))


def format_element(ele, include_rect: bool = False) -> dict:
    """格式化单个元素信息"""
    try:
        attrs = ele.attrs
    except Exception:
        attrs = {}

    info = {
        'tag': ele.tag,
        'text': (ele.raw_text or '').strip()[:200],
        'attrs': attrs,
        'loc': _suggest_locator(ele, attrs),
    }

    if include_rect:
        try:
            info['rect'] = {
                'location': list(ele.rect.location),
                'size': list(ele.rect.size),
                'midpoint': list(ele.rect.midpoint),
            }
        except Exception:
            pass

    return info


def _suggest_locator(ele, attrs: dict) -> str:
    """为元素生成最优 DrissionPage 定位字符串"""
    # 优先用 id
    if attrs.get('id'):
        return f'#{attrs["id"]}'

    # data-testid / data-qa / aria-label 等语义属性
    for semantic in ('data-testid', 'data-qa', 'aria-label', 'name', 'placeholder'):
        if attrs.get(semantic):
            return f'@{semantic}={attrs[semantic]}'

    # 有唯一 class
    cls = attrs.get('class', '')
    if cls:
        classes = cls.strip().split()
        if classes:
            return f'.{classes[0]}'

    # 按文本
    try:
        txt = (ele.raw_text or '').strip()
        if txt and len(txt) <= 30:
            return f'text:{txt}'
    except Exception:
        pass

    # 最后按 tag
    return f't:{ele.tag}'


def format_page_info(page) -> dict:
    """格式化页面基本信息"""
    return {
        'url': page.url,
        'title': page.title,
        'ready_state': page.states.ready_state,
    }
