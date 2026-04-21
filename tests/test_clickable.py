# -*- coding:utf-8 -*-
"""clickable_js / clickable 模块单元测试（不依赖真实浏览器）"""
from dp_cli.snapshot.clickable_js import (
    DETECT_CLICKABLES_JS,
    CLEANUP_CLICKABLES_JS,
    build_detect_js,
)
from dp_cli.snapshot.clickable import (
    CONFIDENCE_MARKER,
    format_clickable_record,
)


# ─────────────────────────────────────────────────────────────────────────────
# JS 模板生成
# ─────────────────────────────────────────────────────────────────────────────

def test_build_detect_js_defaults():
    js = build_detect_js()
    # 顶层必须有 return 才能被 DrissionPage.run_js 捕获
    assert js.strip().endswith('return __dp_detect_result;')
    # 默认参数
    assert 'const VIEWPORT_ONLY = false;' in js
    assert 'const MAX_ELEMENTS = 1000;' in js
    assert 'const INCLUDE_LOW = false;' in js


def test_build_detect_js_with_options():
    js = build_detect_js(viewport_only=True, max_elements=50, include_low=True)
    assert 'const VIEWPORT_ONLY = true;' in js
    assert 'const MAX_ELEMENTS = 50;' in js
    assert 'const INCLUDE_LOW = true;' in js


def test_cleanup_js_returns_value():
    """清理脚本也必须顶层 return 才能拿到结果。"""
    assert CLEANUP_CLICKABLES_JS.strip().endswith('return __dp_cleanup_result;')
    assert 'data-dp-scan-id' in CLEANUP_CLICKABLES_JS


def test_detect_js_is_raw_string_no_percent_collision():
    """模板填充后不应残留任何未替换的 %(xxx)s。"""
    js = build_detect_js()
    assert '%(' not in js
    # 但应保留正则表达式里的合法字符
    assert 'CLICKABLE_ROLES' in js
    assert 'CLASS_PATTERN' in js


def test_detect_js_covers_vimium_rules():
    """快速核查关键规则都在脚本里，避免被误删。"""
    body = DETECT_CLICKABLES_JS
    # HIGH 级
    assert "tag === 'a'" in body
    assert "tag === 'button'" in body
    assert "tag === 'input'" in body
    assert "tag === 'select'" in body
    assert "contenteditable" in body
    assert "CLICKABLE_ROLES.test" in body
    # MEDIUM 级
    assert "'onclick'" in body
    assert "'jsaction'" in body
    assert "'tabindex'" in body
    assert "'aria-selected'" in body
    # 框架 click 属性（Vue / Angular）
    assert "@click" in body
    assert "v-on:click" in body
    # cursor:pointer 启发式
    assert "isPointer" in body
    assert "CLASS_PATTERN.test" in body
    assert "ICON_CLASS_PATTERN" in body
    assert "parentIsPointer" in body
    # 新字段：zone / iconOnly / shadow DOM
    assert "computeZone" in body
    assert "detectIconOnly" in body
    assert "shadowRoot" in body
    # 可见性
    assert "getClientRects" in body
    assert "getBoundingClientRect" in body
    # 临时属性
    assert "data-dp-scan-id" in body


def test_detect_js_shadow_dom_traversal():
    """Shadow DOM 递归：collectAll 必须检查 shadowRoot。"""
    body = DETECT_CLICKABLES_JS
    assert "collectAll" in body
    assert "shadowRoot" in body
    # 清理脚本也要覆盖 shadow DOM
    assert "shadowRoot" in CLEANUP_CLICKABLES_JS


# ─────────────────────────────────────────────────────────────────────────────
# 置信度标记 & 渲染
# ─────────────────────────────────────────────────────────────────────────────

def test_confidence_markers_defined():
    assert CONFIDENCE_MARKER['high'] == ''
    assert '⚡' in CONFIDENCE_MARKER['medium']
    assert '?' in CONFIDENCE_MARKER['low']


def test_format_clickable_record_high():
    rec = {
        'tag': 'button',
        'confidence': 'high',
        'reason': 'button',
        'text': 'Sign in',
        'label': 'Sign in',
        'zone': 'top-right',
        'iconOnly': False,
        'rect': {'x': 10, 'y': 20, 'w': 80, 'h': 32},
        'locator': '#signin',
    }
    s = format_clickable_record(rec, ref_id=5)
    assert s.startswith('[5] button')
    assert '"Sign in"' in s
    assert '@top-right' in s
    assert '→ #signin' in s
    # 默认紧凑格式不含 reason / 尺寸
    assert 'button,' not in s
    assert '80x32' not in s
    # high 不带置信度标记
    assert '⚡' not in s
    assert '?' not in s


def test_format_clickable_record_verbose_shows_reason_and_size():
    rec = {
        'tag': 'button', 'confidence': 'high', 'reason': 'button',
        'label': 'OK', 'zone': 'center', 'iconOnly': False,
        'rect': {'w': 80, 'h': 32}, 'locator': '#ok',
    }
    s = format_clickable_record(rec, ref_id=1, verbose=True)
    assert '(button, 80x32)' in s


def test_format_clickable_record_icon_only_no_label():
    rec = {
        'tag': 'div',
        'confidence': 'medium',
        'reason': 'cursor+icon',
        'text': '',
        'label': '',
        'zone': 'top-left',
        'iconOnly': True,
        'rect': {'w': 32, 'h': 32},
        'locator': '.sidebar-toggle',
    }
    s = format_clickable_record(rec, ref_id=3)
    assert '⚡' in s
    assert '(icon)' in s
    assert '@top-left' in s
    assert '→ .sidebar-toggle' in s
    # 无 label 不应出现空引号
    assert '""' not in s


def test_format_clickable_record_low_has_marker():
    rec = {
        'tag': 'span',
        'confidence': 'low',
        'reason': 'cursor:pointer',
        'text': 'See more',
        'label': 'See more',
        'zone': 'bottom',
        'iconOnly': False,
        'rect': {'w': 60, 'h': 16},
        'locator': '.more',
    }
    s = format_clickable_record(rec, ref_id=9)
    assert '?' in s
    assert '"See more"' in s
    assert '@bottom' in s
