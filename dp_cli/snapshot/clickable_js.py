# -*- coding:utf-8 -*-
"""
Vimium-C 风格的可点击元素探测 JS 脚本（v2）

改进点（相比 v1）：
  1. 递归遍历 Shadow DOM（open shadow root），React/Vue 组件和 Web Components 都能看见
  2. cursor:pointer 启发式升级：
     满足下列任一 → MEDIUM（默认显示，不再必须 --include-low）：
       · 有 aria-label / title / data-tooltip 等 label 属性
       · 包含 svg / img / i / [class*=icon] 子元素（图标按钮特征）
       · 本身就是 svg / i 标签
       · rect 是小方形（12-80px、宽高差 ≤16）—— 图标按钮尺寸特征
       · class 名匹配 btn/click/toggle/menu... 关键词
     都不满足的普通 cursor:pointer 仍为 LOW
  3. 父子 cursor:pointer 去重更严：子元素覆盖父元素 ≥50% 面积时，父元素让位
  4. 新增字段：
     · label: aria-label / title / svg <title> 等无障碍名（优先于 innerText）
     · zone: 位置区域（top-left / top-right / center / bottom 等 9 宫格）
     · iconOnly: 布尔值，无文字但有图标子元素
  5. 更强的 label 回退：svg > title、子 img alt、data-tooltip、data-tippy-content
  6. 识别更多框架 click 属性：@click（Vue）、v-on:click、(click)（Angular）

注意：DrissionPage.run_js(code) 会把 code 包成函数体，只有顶层 return 才能
得到返回值；IIFE 返回值会被丢弃，所以用 `const __r = ...; return __r;`。
"""

DETECT_CLICKABLES_JS = r"""
const __dp_detect_result = (function() {
  const VIEWPORT_ONLY = %(viewport_only)s;
  const MAX_ELEMENTS = %(max_elements)d;
  const INCLUDE_LOW = %(include_low)s;

  const CLICKABLE_ROLES = /^(button|link|checkbox|radio|combobox|menu|menuitem|menuitemcheckbox|menuitemradio|tab|option|switch|slider|spinbutton|searchbox|textbox|treeitem|row|cell|gridcell|listbox|listitem|article|tooltip)$/i;

  const CLASS_PATTERN = /(?:^|[\s_-])(btn|button|click|action|select|link|menu|toggle|tab|close|open|expand|collapse|dropdown|trigger|hoverable|selectable|clickable|chip|pill|tag|card|avatar|icon|ico)(?:$|[\s_-])/i;

  const ICON_CLASS_PATTERN = /(?:^|[\s_-])(icon|ico|fa|fa-|glyphicon|material-icons|anticon|lucide|svg)/i;

  function isElementVisible(el) {
    const rects = el.getClientRects();
    if (!rects.length) return null;
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return null;
    let style;
    try { style = getComputedStyle(el); } catch (e) { return null; }
    if (style.display === 'none' || style.visibility === 'hidden') return null;
    const op = parseFloat(style.opacity);
    if (op < 0.05) return null;
    return { rect: rect, style: style };
  }

  function inViewport(rect) {
    return rect.top < window.innerHeight && rect.bottom > 0
      && rect.left < window.innerWidth && rect.right > 0;
  }

  function classNameString(el) {
    const cn = el.className;
    if (typeof cn === 'string') return cn;
    if (cn && typeof cn.baseVal === 'string') return cn.baseVal;  // SVGAnimatedString
    return '';
  }

  // 计算 9 宫格区域名（基于元素中心点和视口）
  function computeZone(rect) {
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const W = window.innerWidth || 1, H = window.innerHeight || 1;
    const xPart = cx < W / 3 ? 'left' : cx > W * 2 / 3 ? 'right' : 'center';
    const yPart = cy < H / 3 ? 'top' : cy > H * 2 / 3 ? 'bottom' : 'middle';
    if (yPart === 'middle' && xPart === 'center') return 'center';
    if (yPart === 'middle') return xPart;
    if (xPart === 'center') return yPart;
    return yPart + '-' + xPart;
  }

  // 父元素是否也 cursor:pointer —— 若是则本元素应让位给父（保留最外层 pointer）
  // 这样能避免 <div style="cursor:pointer"><svg><use/></svg></div> 里 svg/use 被重复登记
  function parentIsPointer(el) {
    const p = el.parentElement;
    if (!p) return false;
    try { return getComputedStyle(p).cursor === 'pointer'; } catch (e) { return false; }
  }

  // 获取元素的"无障碍名"——优先级 aria-label > aria-labelledby > 控件专属 > innerText > title/alt > 图标兜底
  function getAccessibleText(el) {
    let t = el.getAttribute && el.getAttribute('aria-label');
    if (t && t.trim()) return t.trim();

    const lbi = el.getAttribute && el.getAttribute('aria-labelledby');
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

    const tag = el.tagName.toLowerCase();
    if (tag === 'input') {
      const type = (el.type || 'text').toLowerCase();
      if (type === 'submit' || type === 'button') return (el.value || '').trim();
      return (el.getAttribute('placeholder') || el.getAttribute('aria-placeholder') || '').trim();
    }

    // innerText（优先取，很多图标按钮实际上有 sr-only 文字）
    const it = ((el.innerText || el.textContent) || '').trim().replace(/\s+/g, ' ');
    if (it && it.length <= 120) return it;
    if (it) return it.slice(0, 120);

    // 各种 tooltip / title 属性
    t = el.getAttribute('title')
      || el.getAttribute('alt')
      || el.getAttribute('data-tooltip')
      || el.getAttribute('data-tippy-content')
      || el.getAttribute('data-original-title')
      || el.getAttribute('data-bs-original-title')
      || el.getAttribute('data-title');
    if (t && t.trim()) return t.trim();

    // svg > title 子节点
    try {
      const svgTitle = el.querySelector('svg > title, svg > desc');
      if (svgTitle) {
        const st = (svgTitle.textContent || '').trim();
        if (st) return st;
      }
      // 子节点 aria-label
      const innerLabeled = el.querySelector('[aria-label]');
      if (innerLabeled) {
        const al = innerLabeled.getAttribute('aria-label');
        if (al && al.trim()) return al.trim();
      }
      // 子 img alt
      const imgAlt = el.querySelector('img[alt]');
      if (imgAlt) {
        const alt = imgAlt.getAttribute('alt');
        if (alt && alt.trim()) return alt.trim();
      }
    } catch (e) {}

    return '';
  }

  // 判断是否是"仅图标"的按钮（无有效文字，但包含图标子元素）
  function detectIconOnly(el, accessibleText) {
    if (accessibleText) return false;
    try {
      if (el.querySelector('svg, img, :scope > i, :scope > [class*="icon"], :scope > [class*="Icon"]')) {
        return true;
      }
    } catch (e) {}
    return false;
  }

  // 核心分类
  function classify(el, style, rect) {
    const tag = el.tagName.toLowerCase();

    // HIGH 级：原生交互标签
    if (tag === 'a') {
      if (!el.hasAttribute('href') && !el.hasAttribute('onclick')) return null;
      return { confidence: 'high', reason: 'a' };
    }
    if (tag === 'button' && !el.disabled) return { confidence: 'high', reason: 'button' };
    if (tag === 'select' && !el.disabled) return { confidence: 'high', reason: 'select' };
    if (tag === 'textarea' && !el.disabled) {
      return { confidence: 'high', reason: el.readOnly ? 'textarea[ro]' : 'textarea' };
    }
    if (tag === 'input' && !el.disabled) {
      const type = (el.type || 'text').toLowerCase();
      if (type === 'hidden') return null;
      return { confidence: 'high', reason: 'input[' + type + ']' };
    }
    if (tag === 'summary' || tag === 'details') {
      return { confidence: 'high', reason: tag };
    }
    if (tag === 'label') {
      if (el.htmlFor || el.control) return { confidence: 'high', reason: 'label' };
      return null;
    }
    if (tag === 'audio' || tag === 'video') {
      return { confidence: 'medium', reason: tag };
    }
    if (tag === 'iframe' || tag === 'frame') {
      return { confidence: 'low', reason: tag };
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

    // MEDIUM 级：显式事件 / 框架 click / tabindex / aria-selected
    if (el.hasAttribute('onclick') || el.hasAttribute('onmousedown') || el.hasAttribute('onpointerdown')) {
      return { confidence: 'medium', reason: 'onclick-attr' };
    }
    if (el.hasAttribute('jsaction')) {
      return { confidence: 'medium', reason: 'jsaction' };
    }
    if (el.hasAttribute('ng-click') || el.hasAttribute('(click)')
        || el.hasAttribute('@click') || el.hasAttribute('v-on:click')) {
      return { confidence: 'medium', reason: 'fw-click' };
    }
    const ti = el.getAttribute('tabindex');
    if (ti !== null) {
      const tiNum = parseInt(ti, 10);
      if (!isNaN(tiNum) && tiNum >= 0) {
        return { confidence: 'medium', reason: 'tabindex=' + tiNum };
      }
    }
    if (el.hasAttribute('aria-selected') || el.hasAttribute('aria-checked')) {
      return { confidence: 'medium', reason: 'aria-state' };
    }

    // cursor:pointer + 启发式 —— 这是 React/Vue 图标按钮的主要特征
    const isPointer = style.cursor === 'pointer';
    if (isPointer) {
      // 父元素也是 cursor:pointer → 让位给父（保留最外层，Vimium 同策略）
      if (parentIsPointer(el)) return null;

      const ariaLabel = el.getAttribute('aria-label');
      const title = el.getAttribute('title');
      const tooltip = el.getAttribute('data-tooltip')
        || el.getAttribute('data-tippy-content')
        || el.getAttribute('data-original-title');
      const hasLabel = !!(ariaLabel || title || tooltip);

      let hasIconChild = false;
      try {
        hasIconChild = !!el.querySelector('svg, img, i.fa, i[class*="icon"], [class*="icon"]:not(body)');
      } catch (e) {}

      const isIconTag = tag === 'svg' || tag === 'i';
      const widthOk = rect.width >= 12 && rect.width <= 80;
      const heightOk = rect.height >= 12 && rect.height <= 80;
      const aspectOk = Math.abs(rect.width - rect.height) <= 24;
      const smallSquare = widthOk && heightOk && aspectOk;

      const cn = classNameString(el);
      const hasClassHint = cn && (CLASS_PATTERN.test(cn) || ICON_CLASS_PATTERN.test(cn));

      if (hasLabel) return { confidence: 'medium', reason: 'cursor+label' };
      if (hasIconChild && smallSquare) return { confidence: 'medium', reason: 'cursor+icon' };
      if (hasIconChild) return { confidence: 'medium', reason: 'cursor+icon-child' };
      if (isIconTag) return { confidence: 'medium', reason: 'cursor+' + tag };
      if (smallSquare) return { confidence: 'medium', reason: 'cursor+square' };
      if (hasClassHint) return { confidence: 'medium', reason: 'cursor+class' };

      // 兜底：普通 cursor:pointer（文字链接类样式） → LOW
      if (!INCLUDE_LOW) return null;
      return { confidence: 'low', reason: 'cursor:pointer' };
    }

    // LOW 级：class 名关键词匹配（无 cursor:pointer）
    if (!INCLUDE_LOW) return null;
    const cn = classNameString(el);
    if (cn && CLASS_PATTERN.test(cn)) {
      const p = el.parentElement;
      if (!p || !CLASS_PATTERN.test(classNameString(p))) {
        return { confidence: 'low', reason: 'class-pattern' };
      }
    }

    return null;
  }

  // 遍历所有元素（含 open Shadow DOM）
  function collectAll() {
    const out = [];
    function walk(root) {
      let nodes;
      try { nodes = root.querySelectorAll('*'); } catch (e) { return; }
      for (let i = 0; i < nodes.length; i++) {
        const el = nodes[i];
        out.push(el);
        if (el.shadowRoot) {
          try { walk(el.shadowRoot); } catch (e) {}
        }
      }
    }
    walk(document);
    return out;
  }

  // 清理上次残留
  try {
    document.querySelectorAll('[data-dp-scan-id]').forEach(el => el.removeAttribute('data-dp-scan-id'));
  } catch (e) {}

  const results = [];
  let counter = 0;
  let truncated = false;

  const all = collectAll();
  for (let i = 0; i < all.length; i++) {
    if (results.length >= MAX_ELEMENTS) { truncated = true; break; }
    const el = all[i];

    const vis = isElementVisible(el);
    if (!vis) continue;
    const rect = vis.rect;
    const style = vis.style;

    if (VIEWPORT_ONLY && !inViewport(rect)) continue;

    const cls = classify(el, style, rect);
    if (!cls) continue;

    counter++;
    try { el.setAttribute('data-dp-scan-id', String(counter)); } catch (e) { continue; }

    const text = getAccessibleText(el).slice(0, 150);
    const iconOnly = detectIconOnly(el, text);
    const zone = computeZone(rect);

    results.push({
      scanId: counter,
      tag: el.tagName.toLowerCase(),
      confidence: cls.confidence,
      reason: cls.reason,
      text: text,
      label: text,
      iconOnly: iconOnly,
      zone: zone,
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

CLEANUP_CLICKABLES_JS = r"""
const __dp_cleanup_result = (function() {
  let n = 0;
  function walk(root) {
    let nodes;
    try { nodes = root.querySelectorAll('[data-dp-scan-id]'); } catch (e) { return; }
    nodes.forEach(el => { el.removeAttribute('data-dp-scan-id'); n++; });
    // 清理 shadow DOM 里的
    try {
      const all = root.querySelectorAll('*');
      for (let i = 0; i < all.length; i++) {
        if (all[i].shadowRoot) walk(all[i].shadowRoot);
      }
    } catch (e) {}
  }
  walk(document);
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
