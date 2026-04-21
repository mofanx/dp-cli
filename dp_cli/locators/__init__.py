# -*- coding:utf-8 -*-
"""Playwright 风格定位器（pw: 前缀）。

模块入口只暴露最常用的 API：
  - parse_pw(expr): 解析 'css=.btn >> role=button[name="OK"]' → matcher 列表
  - build_pw_js(matchers): 把 matcher 列表转成可执行的 JS 脚本
"""
from .playwright import parse_pw, PwParseError  # noqa: F401
from .pw_js import build_pw_js  # noqa: F401
