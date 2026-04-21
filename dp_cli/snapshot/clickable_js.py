# -*- coding:utf-8 -*-
"""
Vimium-C 风格的可点击元素探测 JS 脚本

运行在浏览器内通过 CDP Runtime.evaluate；遍历可见 DOM 节点，
按多级规则识别可交互元素并打上临时属性 `data-dp-scan-id`，便于
Python 端通过 CDP DOM 树将其映射到 backendNodeId。

三级置信度：
  high    — 明确可点击（<a href>, <button>, role=button 等）
  medium  — 很可能可点击（onclick/jsaction/tabindex>=0/aria-selected）
  low     — 启发式（cursor:pointer 或 class 名匹配 btn/click/… 规则）

规则参考：vimium-c/content/local_links.ts
"""

# 探测脚本：返回 {elements: [...], total: N, truncated: bool}
# 模板占位符：%(viewport_only)s, %(max_elements)d, %(include_low)s
#
# 注意：DrissionPage.run_js(code) 会把 code 包成函数体，只有顶层 return 才能
# 获得返回值；单纯 IIFE `(()=>{...})()` 的返回值会被丢弃。所以下面用
# `const __r = (function(){...})(); return __r;` 的形式。
DETECT_CLICKABLES_JS = r"""
const __dp_detect_result = (function() {
  const VIEWPORT_ONLY = %(viewport_only)s;
  const MAX_ELEMENTS = %(max_elements)d;
  const INCLUDE_LOW = %(include_low)s;

  const CLICKABLE_ROLES = /^(button|link|checkbox|radio|combobox|menu|menuitem|menuitemcheckbox|menuitemradio|tab|option|switch|slider|spinbutton|searchbox|textbox|treeitem|row|cell|gridcell|listbox|listitem)$/i;

  // 类名关键词正则：btn/button/click/action/link/menu/toggle/tab/close/open/expand/collapse/dropdown/trigger
  const CLASS_PATTERN = /(?:^|[\s_-])(btn|button|click|action|select|link|menu|toggle|tab|close|open|expand|collapse|dropdown|trigger|hoverable|selectable|clickable)(?:$|[\s_-])/i;

  // 非可编辑 input 类型
  const UNEDITABLE_INPUT = new Set(['hidden', 'submit', 'reset', 'button', 'checkbox', 'radio', 'image', 'file', 'color', 'range']);

  function isElementVisible(el) {
    const rects = el.getClientRects();
    if (!rects.length) return null;
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return null;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return null;
    const op = parseFloat(style.opacity);
    if (op < 0.05) return null;
    return rect;
  }

  function inViewport(rect) {
    return rect.top < window.innerHeight && rect.bottom > 0
      && rect.left < window.innerWidth && rect.right > 0;
  }

  function classNameString(el) {
    const cn = el.className;
    if (typeof cn === 'string') return cn;
    // SVGAnimatedString
    if (cn && typeof cn.baseVal === 'string') return cn.baseVal;
    return '';
  }

  function parentHasMatchingCursor(el) {
    const p = el.parentElement;
    if (!p) return false;
    try { return getComputedStyle(p).cursor === 'pointer'; } catch (e) { return false; }
  }

  function parentHasMatchingClass(el) {
    const p = el.parentElement;
    if (!p) return false;
    return CLASS_PATTERN.test(classNameString(p));
  }

  // 判断一个元素是否可交互，返回 {confidence, reason} 或 null
  function classify(el) {
    const tag = el.tagName.toLowerCase();

    // HIGH
    if (tag === 'a') {
      if (!el.hasAttribute('href') && !el.hasAttribute('onclick')) return null;
      return { confidence: 'high', reason: 'a' };
    }
    if (tag === 'button') {
      if (el.disabled) return null;
      return { confidence: 'high', reason: 'button' };
    }
    if (tag === 'select') {
      if (el.disabled) return null;
      return { confidence: 'high', reason: 'select' };
    }
    if (tag === 'textarea') {
      if (el.disabled) return null;
      return { confidence: 'high', reason: el.readOnly ? 'textarea[readonly]' : 'textarea' };
    }
    if (tag === 'input') {
      if (el.disabled) return null;
      const type = (el.type || 'text').toLowerCase();
      if (type === 'hidden') return null;
      return { confidence: 'high', reason: 'input[' + type + ']' };
    }
    if (tag === 'summary' || tag === 'details') {
      return { confidence: 'high', reason: tag };
    }
    if (tag === 'label') {
      // 仅当关联到实际控件时，<label> 本身可点击触发控件
      if (el.htmlFor || el.control) return { confidence: 'high', reason: 'label' };
      // 没有关联的 label 不登记（点击无效）
      return null;
    }
    if (tag === 'audio' || tag === 'video') {
      return { confidence: 'medium', reason: tag };
    }
    if (tag === 'iframe' || tag === 'frame') {
      return { confidence: 'low', reason: tag };  // 一般不直接点击
    }

    // contenteditable
    const ce = el.getAttribute('contenteditable');
    if (ce !== null && ce !== 'false' && ce !== 'inherit') {
      return { confidence: 'high', reason: 'contenteditable' };
    }

    // role 白名单
    const role = el.getAttribute('role');
    if (role && CLICKABLE_ROLES.test(role.trim())) {
      return { confidence: 'high', reason: 'role=' + role };
    }

    // onclick / onmousedown / jsaction / ng-click
    if (el.hasAttribute('onclick') || el.hasAttribute('onmousedown')) {
      return { confidence: 'medium', reason: 'onclick-attr' };
    }
    if (el.hasAttribute('jsaction')) {
      return { confidence: 'medium', reason: 'jsaction' };
    }
    if (el.hasAttribute('ng-click') || el.hasAttribute('(click)')) {
      return { confidence: 'medium', reason: 'framework-click' };
    }

    // tabindex >= 0
    const ti = el.getAttribute('tabindex');
    if (ti !== null) {
      const tiNum = parseInt(ti, 10);
      if (!isNaN(tiNum) && tiNum >= 0) {
        return { confidence: 'medium', reason: 'tabindex=' + tiNum };
      }
    }

    // aria-selected / aria-checked
    if (el.hasAttribute('aria-selected') || el.hasAttribute('aria-checked')) {
      return { confidence: 'medium', reason: 'aria-selected' };
    }

    // LOW 级需要 include_low 开关
    if (!INCLUDE_LOW) return null;

    // cursor:pointer（父元素也 pointer 则跳过避免冗余）
    let style;
    try { style = getComputedStyle(el); } catch (e) { return null; }
    if (style.cursor === 'pointer' && !parentHasMatchingCursor(el)) {
      return { confidence: 'low', reason: 'cursor:pointer' };
    }

    // class 名关键词
    const cn = classNameString(el);
    if (cn && CLASS_PATTERN.test(cn) && !parentHasMatchingClass(el)) {
      return { confidence: 'low', reason: 'class-pattern' };
    }

    // SVG 且 cursor:pointer（上面已处理，但 SVG 有时 cursor 继承）
    if (tag === 'svg' && style.cursor === 'pointer') {
      return { confidence: 'low', reason: 'svg-cursor' };
    }

    return null;
  }

  function getAccessibleText(el) {
    // aria-label 优先
    let t = el.getAttribute('aria-label');
    if (t) return t.trim();
    // aria-labelledby
    const lbi = el.getAttribute('aria-labelledby');
    if (lbi) {
      const ids = lbi.split(/\s+/);
      const texts = [];
      for (const id of ids) {
        const ref = document.getElementById(id);
        if (ref) texts.push((ref.innerText || ref.textContent || '').trim());
      }
      const joined = texts.filter(Boolean).join(' ').trim();
      if (joined) return joined;
    }
    // input 的 value / placeholder / type
    const tag = el.tagName.toLowerCase();
    if (tag === 'input') {
      const type = (el.type || 'text').toLowerCase();
      if (type === 'submit' || type === 'button') return (el.value || '').trim();
      return (el.getAttribute('placeholder') || el.getAttribute('aria-placeholder') || '').trim();
    }
    // innerText（截断）
    const it = (el.innerText || '').trim().replace(/\s+/g, ' ');
    if (it) return it.slice(0, 120);
    // title / alt
    return (el.getAttribute('title') || el.getAttribute('alt') || '').trim();
  }

  // 去掉之前可能残留的 data-dp-scan-id（比如上次调用异常中断）
  document.querySelectorAll('[data-dp-scan-id]').forEach(el => el.removeAttribute('data-dp-scan-id'));

  const results = [];
  let counter = 0;
  let truncated = false;

  // 遍历所有元素
  const all = document.querySelectorAll('*');
  for (let i = 0; i < all.length; i++) {
    if (results.length >= MAX_ELEMENTS) { truncated = true; break; }
    const el = all[i];

    const cls = classify(el);
    if (!cls) continue;

    const rect = isElementVisible(el);
    if (!rect) continue;

    if (VIEWPORT_ONLY && !inViewport(rect)) continue;

    counter++;
    try { el.setAttribute('data-dp-scan-id', String(counter)); } catch (e) { continue; }

    results.push({
      scanId: counter,
      tag: el.tagName.toLowerCase(),
      confidence: cls.confidence,
      reason: cls.reason,
      text: getAccessibleText(el).slice(0, 150),
      rect: {
        x: Math.round(rect.left),
        y: Math.round(rect.top),
        w: Math.round(rect.width),
        h: Math.round(rect.height)
      },
      inViewport: inViewport(rect)
    });
  }

  return { elements: results, total: results.length, truncated: truncated };
})();
return __dp_detect_result;
"""

# 清理脚本：移除所有 data-dp-scan-id 属性
CLEANUP_CLICKABLES_JS = r"""
const __dp_cleanup_result = (function() {
  const nodes = document.querySelectorAll('[data-dp-scan-id]');
  let n = 0;
  nodes.forEach(el => { el.removeAttribute('data-dp-scan-id'); n++; });
  return { cleaned: n };
})();
return __dp_cleanup_result;
"""


def build_detect_js(viewport_only: bool = False,
                    max_elements: int = 1000,
                    include_low: bool = False) -> str:
    """按参数填充模板，返回可注入的 JS 代码。"""
    return DETECT_CLICKABLES_JS % {
        'viewport_only': 'true' if viewport_only else 'false',
        'max_elements': int(max_elements),
        'include_low': 'true' if include_low else 'false',
    }
