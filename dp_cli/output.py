# -*- coding:utf-8 -*-
"""
dp-cli 输出格式化模块
统一的 JSON 输出格式，便于 AI 工具解析。
"""
import json
import sys
from typing import Any, Optional
from dp_cli.snapshot.utils import suggest_locator


def ok(data: Any = None, msg: str = None) -> None:
    """成功输出"""
    result = {'status': 'ok'}
    if msg:
        result['message'] = msg
    if data is not None:
        result['data'] = data
    print(json.dumps(result, ensure_ascii=False, indent=2))


def error(msg: str, code: str = 'ERROR', detail: str = None) -> None:
    """错误输出"""
    result = {'status': 'error', 'code': code, 'message': msg}
    if detail:
        result['detail'] = detail
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1)


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
        'loc': suggest_locator(ele.tag, attrs, (ele.raw_text or '').strip()[:50]),
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


def format_page_info(page) -> dict:
    """格式化页面基本信息"""
    return {
        'url': page.url,
        'title': page.title,
        'ready_state': page.states.ready_state,
    }
