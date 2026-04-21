# -*- coding:utf-8 -*-
"""
dp-cli snapshot 模块

基于浏览器原生 a11y tree（无障碍树）的页面快照系统。
通过 CDP Accessibility API 获取，通用性极强，适用于 95%+ 的网站。

模块组成：
  - a11y.py          核心：a11y tree 获取 + 多模式渲染（full/brief/text）
  - clickable.py     Vimium 风格可交互元素探测（补充 a11y tree 覆盖盲区）
  - clickable_js.py  注入浏览器的 JS 探测脚本
  - extract.py       数据提取（extract_structured / query_elements）
  - utils.py         共享工具（定位器生成等）
  - js_scripts.py    JS 降级脚本（CDP 不可用时的 fallback）
"""
from .a11y import take_a11y_snapshot, render_a11y_text, render_a11y_plain_text
from .clickable import detect_clickables, format_clickable_record
from .extract import extract_structured, query_elements

__all__ = [
    'take_a11y_snapshot',
    'render_a11y_text',
    'render_a11y_plain_text',
    'detect_clickables',
    'format_clickable_record',
    'extract_structured',
    'query_elements',
]
