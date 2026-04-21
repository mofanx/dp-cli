# -*- coding:utf-8 -*-
"""pw 表达式求值 JS 脚本。

核心思路：matchers 数组链式应用，scope 从 [documentElement] 开始，每一步
matcher 产生新 scope（element 数组），最终取 scope[0]。找到后给元素打上
临时 data-dp-ref 属性，返回 marker 字符串 → Python 侧用 @data-dp-ref=X
定位（复用 0.3.2 的打标链路）。

返回：
  - 成功：marker 字符串（12 位 hex）
  - 未匹配到：null

实现细节：
  - Shadow DOM 递归（跟 clickable_js.py 一致）
  - role 映射 ARIA → native tag
  - accessibleName 简化实现：aria-label → labelledby → labels → value/text
  - text 匹配遵循 Playwright "deepest first" 语义（去除祖先）
  - 正则 flags 仅支持 JS 合法的 gimsuy
"""
import json

# JS 脚本：接收 __MATCHERS_JSON__ 占位符（双 JSON 编码后插入）
_PW_RESOLVE_JS_TEMPLATE = r"""
const __dp_pw_result = (function() {
  const MATCHERS = JSON.parse(__MATCHERS_JSON__);

  // ── 工具函数 ──────────────────────────────────────────────────────────
  function qAllDeep(root, selector) {
    // querySelectorAll + 递归进入 open shadow root
    const out = [];
    function walk(node) {
      if (!node) return;
      try {
        if (node.querySelectorAll) {
          const matched = node.querySelectorAll(selector);
          for (let i = 0; i < matched.length; i++) out.push(matched[i]);
        }
      } catch (e) {}
      try {
        const all = node.querySelectorAll ? node.querySelectorAll('*') : [];
        for (let i = 0; i < all.length; i++) {
          if (all[i].shadowRoot) walk(all[i].shadowRoot);
        }
      } catch (e) {}
    }
    walk(root);
    return out;
  }

  function allDescendants(root) {
    const out = [];
    function walk(node) {
      if (!node) return;
      try {
        const all = node.querySelectorAll ? node.querySelectorAll('*') : [];
        for (let i = 0; i < all.length; i++) {
          out.push(all[i]);
          if (all[i].shadowRoot) walk(all[i].shadowRoot);
        }
      } catch (e) {}
    }
    walk(root);
    return out;
  }

  function isVisible(el) {
    const r = el.getClientRects();
    if (!r || !r.length) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return false;
    let s;
    try { s = getComputedStyle(el); } catch (e) { return true; }
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    return parseFloat(s.opacity) >= 0.01;
  }

  // 语义可见：过滤掉 display:none 祖先链 / hidden 属性 / aria-hidden=true
  // （跟 Playwright 的 text/has-text 默认过滤一致；比 isVisible 宽松）
  function isSemanticallyVisible(el) {
    let node = el;
    while (node && node.nodeType === 1) {
      let s;
      try { s = getComputedStyle(node); } catch (e) { return true; }
      if (s && s.display === 'none') return false;
      if (s && s.visibility === 'hidden') return false;
      if (node.hasAttribute && node.hasAttribute('hidden')) return false;
      if (node.getAttribute && node.getAttribute('aria-hidden') === 'true') {
        return false;
      }
      node = node.parentElement;
    }
    return true;
  }

  function dedupe(arr) {
    const seen = new Set();
    const out = [];
    for (const e of arr) {
      if (!seen.has(e)) { seen.add(e); out.push(e); }
    }
    return out;
  }

  // 值规格匹配： {kind: 'exact'|'substr'|'regex', value, flags?}
  function matchStr(actual, spec) {
    if (spec == null) return true;
    if (actual == null) return false;
    actual = String(actual).trim().replace(/\s+/g, ' ');
    if (spec.kind === 'exact') return actual === spec.value;
    if (spec.kind === 'substr') {
      return actual.toLowerCase().indexOf(String(spec.value).toLowerCase()) >= 0;
    }
    if (spec.kind === 'regex') {
      try {
        const re = new RegExp(spec.value, spec.flags || '');
        return re.test(actual);
      } catch (e) { return false; }
    }
    return false;
  }

  // ── accessible name（简化实现） ──────────────────────────────────────
  function accName(el) {
    let t = el.getAttribute && el.getAttribute('aria-label');
    if (t && t.trim()) return t.trim();

    const lbi = el.getAttribute && el.getAttribute('aria-labelledby');
    if (lbi) {
      const parts = [];
      for (const id of lbi.split(/\s+/)) {
        const r = document.getElementById(id);
        if (r) parts.push((r.innerText || r.textContent || '').trim());
      }
      const joined = parts.filter(Boolean).join(' ').trim();
      if (joined) return joined;
    }

    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'input') {
      const type = (el.type || 'text').toLowerCase();
      if (type === 'button' || type === 'submit' || type === 'reset') {
        return (el.value || '').trim();
      }
      if (el.labels && el.labels.length) {
        return Array.from(el.labels).map(l =>
          (l.innerText || l.textContent || '').trim()
        ).filter(Boolean).join(' ');
      }
      return (el.getAttribute('placeholder') || '').trim();
    }

    if (tag === 'img' || tag === 'area' || tag === 'input') {
      const alt = el.getAttribute('alt');
      if (alt && alt.trim()) return alt.trim();
    }

    // 关联的 <label>（对非 input 也可能有）
    if (el.labels && el.labels.length) {
      const lb = Array.from(el.labels).map(l =>
        (l.innerText || l.textContent || '').trim()
      ).filter(Boolean).join(' ');
      if (lb) return lb;
    }

    const it = ((el.innerText || el.textContent) || '').trim()
      .replace(/\s+/g, ' ');
    if (it) return it.length > 200 ? it.slice(0, 200) : it;

    t = el.getAttribute && (el.getAttribute('title')
        || el.getAttribute('alt'));
    if (t && t.trim()) return t.trim();

    // 子 svg <title>
    try {
      const st = el.querySelector && el.querySelector('svg > title');
      if (st) {
        const s = (st.textContent || '').trim();
        if (s) return s;
      }
    } catch (e) {}

    return '';
  }

  // ── role → 候选选择器（含原生标签） ───────────────────────────────────
  const ROLE_NATIVE = {
    button: ['button', 'input[type="button"]', 'input[type="submit"]',
             'input[type="reset"]', 'summary'],
    link: ['a[href]', 'area[href]'],
    textbox: ['input:not([type])', 'input[type="text"]',
              'input[type="search"]', 'input[type="email"]',
              'input[type="tel"]', 'input[type="url"]',
              'input[type="password"]', 'input[type="number"]',
              'textarea'],
    searchbox: ['input[type="search"]'],
    checkbox: ['input[type="checkbox"]'],
    radio: ['input[type="radio"]'],
    combobox: ['select'],
    option: ['option'],
    list: ['ul', 'ol', 'menu'],
    listitem: ['li'],
    listbox: ['datalist'],
    img: ['img[alt]:not([alt=""])', 'img', 'svg'],
    heading: ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'],
    paragraph: ['p'],
    article: ['article'],
    banner: ['header'],
    navigation: ['nav'],
    main: ['main'],
    contentinfo: ['footer'],
    complementary: ['aside'],
    region: ['section[aria-label]', 'section[aria-labelledby]'],
    dialog: ['dialog'],
    table: ['table'],
    row: ['tr'],
    cell: ['td'],
    columnheader: ['th[scope="col"]', 'thead th'],
    rowheader: ['th[scope="row"]'],
    form: ['form[aria-label]', 'form[aria-labelledby]'],
    separator: ['hr']
  };

  function findByRole(root, role, nameSpec) {
    const selectors = ['[role~="' + role + '"]'];
    const natives = ROLE_NATIVE[role] || [];
    for (const n of natives) selectors.push(n);

    const pool = dedupe(selectors.flatMap(sel => {
      try { return qAllDeep(root, sel); } catch (e) { return []; }
    }));

    // 默认过滤隐藏元素（display:none 链 / hidden / aria-hidden=true）
    const filtered = pool.filter(isSemanticallyVisible);

    if (!nameSpec) return filtered;
    return filtered.filter(el => matchStr(accName(el), nameSpec));
  }

  // ── text 匹配：Playwright deepest-first 语义 ──────────────────────────
  function findByText(root, spec) {
    const all = allDescendants(root);
    const matched = all.filter(el => {
      if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE') return false;
      if (!isSemanticallyVisible(el)) return false;
      const it = (el.innerText || el.textContent || '').trim()
        .replace(/\s+/g, ' ');
      if (!it) return false;
      return matchStr(it, spec);
    });
    // 保留 deepest：去掉祖先包含其他 matched 的那些
    return matched.filter(el =>
      !matched.some(other => other !== el && el.contains(other))
    );
  }

  // ── label 匹配 ───────────────────────────────────────────────────────
  function findByLabel(root, spec) {
    const labels = qAllDeep(root, 'label');
    const results = [];
    for (const lb of labels) {
      const txt = (lb.innerText || lb.textContent || '').trim();
      if (!matchStr(txt, spec)) continue;
      let ctl = lb.control;
      if (!ctl && lb.htmlFor) ctl = document.getElementById(lb.htmlFor);
      if (!ctl) {
        ctl = lb.querySelector('input, textarea, select, button,'
          + '[contenteditable], [role="textbox"], [role="combobox"]');
      }
      if (ctl) results.push(ctl);
    }
    // aria-labelledby 关联
    const labeled = qAllDeep(root, '[aria-labelledby]');
    for (const el of labeled) {
      const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/);
      const texts = ids.map(id => {
        const r = document.getElementById(id);
        return r ? (r.innerText || r.textContent || '').trim() : '';
      }).filter(Boolean);
      if (texts.some(t => matchStr(t, spec))) results.push(el);
    }
    // aria-label 自身
    const selfLabeled = qAllDeep(root, '[aria-label]');
    for (const el of selfLabeled) {
      if (matchStr(el.getAttribute('aria-label') || '', spec)) {
        results.push(el);
      }
    }
    return dedupe(results);
  }

  // ── 按属性（placeholder / alt / title / testid） ─────────────────────
  function findByAttr(root, attrName, spec) {
    const pool = qAllDeep(root, '[' + attrName + ']');
    return pool.filter(el =>
      matchStr(el.getAttribute(attrName) || '', spec)
    );
  }

  // ── has-text 过滤器 ───────────────────────────────────────────────────
  function filterHasText(scope, spec) {
    return scope.filter(el => {
      if (!isSemanticallyVisible(el)) return false;
      const it = (el.innerText || el.textContent || '').trim()
        .replace(/\s+/g, ' ');
      return it && matchStr(it, spec);
    });
  }

  // ── xpath 结果提取 ───────────────────────────────────────────────────
  function findByXPath(root, xp) {
    const contextNode = (root === document || root.nodeType === 9)
      ? document : root;
    const res = document.evaluate(xp, contextNode,
      null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
    const out = [];
    for (let i = 0; i < res.snapshotLength; i++) {
      const n = res.snapshotItem(i);
      if (n && n.nodeType === 1) out.push(n);
    }
    return out;
  }

  // ── 应用单个 matcher 到单个 root ─────────────────────────────────────
  function applyMatcher(root, m) {
    if (m.type === 'css') {
      try { return qAllDeep(root, m.value); } catch (e) { return []; }
    }
    if (m.type === 'xpath') {
      try { return findByXPath(root, m.value); } catch (e) { return []; }
    }
    if (m.type === 'role') return findByRole(root, m.role, m.name);
    if (m.type === 'text') return findByText(root, m.value);
    if (m.type === 'label') return findByLabel(root, m.value);
    if (m.type === 'placeholder') return findByAttr(root, 'placeholder', m.value);
    if (m.type === 'alt') return findByAttr(root, 'alt', m.value);
    if (m.type === 'title') return findByAttr(root, 'title', m.value);
    if (m.type === 'testid') {
      // data-testid > data-test-id > data-test
      const a = findByAttr(root, 'data-testid', m.value);
      const b = findByAttr(root, 'data-test-id', m.value);
      const c = findByAttr(root, 'data-test', m.value);
      return dedupe(a.concat(b).concat(c));
    }
    return [];
  }

  // ── 主循环：逐 matcher 应用 ──────────────────────────────────────────
  let scope = [document.documentElement];

  for (const m of MATCHERS) {
    if (m.type === 'nth') {
      const idx = m.index < 0 ? scope.length + m.index : m.index;
      scope = (idx >= 0 && idx < scope.length) ? [scope[idx]] : [];
    } else if (m.type === 'has-text') {
      scope = filterHasText(scope, m.value);
    } else if (m.type === 'visible') {
      scope = scope.filter(el => isVisible(el) === m.value);
    } else {
      const next = [];
      for (const root of scope) {
        const found = applyMatcher(root, m);
        for (const el of found) next.push(el);
      }
      scope = dedupe(next);
    }
    if (!scope.length) break;
  }

  if (!scope.length) return null;
  const el = scope[0];

  // 给元素打临时 data-dp-ref 属性
  const marker = 'dp' + Math.random().toString(36).slice(2, 14).padEnd(12, '0');
  try {
    el.setAttribute('data-dp-ref', marker);
  } catch (e) {
    return null;
  }
  return marker;
})();
return __dp_pw_result;
"""


def build_pw_js(matchers: list) -> str:
    """把 matcher 列表嵌入 JS 模板，返回可直接 page.run_js() 的脚本。

    做双 JSON 编码：
      第一次 dumps → 得到 JSON 字符串（matchers 的序列化）
      第二次 dumps → 把该字符串变成合法的 JS 字符串字面量
    这样 `JSON.parse(__MATCHERS_JSON__)` 就能在 JS 里还原成对象。
    """
    matchers_json = json.dumps(matchers, ensure_ascii=False)
    js_literal = json.dumps(matchers_json, ensure_ascii=False)
    return _PW_RESOLVE_JS_TEMPLATE.replace('__MATCHERS_JSON__', js_literal)
