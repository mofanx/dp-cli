# -*- coding:utf-8 -*-
"""
dp-cli snapshot 模块（v2 重构版）

核心设计：
  1. 页面结构分析：基于 DOM JS 识别模块（导航/搜索/列表/内容/页脚）
  2. 交互元素按区域分组：不再是扁平列表，而是归属到页面模块
  3. 重复模式检测：自动识别列表，生成自动化提示
  4. 主体内容提取：评分公式找最佳内容区块，输出 markdown

模块组成：
  - core.py       take_snapshot() 主函数
  - js_scripts.py JS 脚本常量
  - render.py     文本渲染
  - extract.py    数据提取（extract_structured / query_elements）
  - utils.py      共享工具
  - a11y.py       a11y tree（CDP 原生无障碍树）
"""
from .core import take_snapshot
from .render import render_snapshot_text
from .extract import extract_structured, query_elements
from .a11y import take_a11y_snapshot, render_a11y_text

__all__ = [
    'take_snapshot',
    'render_snapshot_text',
    'extract_structured',
    'query_elements',
    'take_a11y_snapshot',
    'render_a11y_text',
]
