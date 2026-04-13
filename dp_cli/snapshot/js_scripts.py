# -*- coding:utf-8 -*-
"""
浏览器注入 JS 脚本。

_JS_A11Y_FALLBACK — 当 CDP Accessibility API 不可用时，通过 DOM 遍历模拟 a11y tree。
"""

# ── A11y Tree JS 降级脚本 ─────────────────────────────────────────────────────
# 当 CDP Accessibility API 不可用时，通过 DOM 遍历模拟 a11y tree
_JS_A11Y_FALLBACK = """
(() => {
    const ROLE_MAP = {
        a: 'link', button: 'button', input: 'textbox', textarea: 'textbox',
        select: 'combobox', option: 'option', img: 'img', nav: 'navigation',
        main: 'main', header: 'banner', footer: 'contentinfo', aside: 'complementary',
        form: 'form', table: 'table', tr: 'row', td: 'cell', th: 'columnheader',
        ul: 'list', ol: 'list', li: 'listitem', h1: 'heading', h2: 'heading',
        h3: 'heading', h4: 'heading', h5: 'heading', h6: 'heading',
        article: 'article', section: 'region', dialog: 'dialog',
        details: 'group', summary: 'button', progress: 'progressbar',
        meter: 'meter', output: 'status',
    };
    const INPUT_ROLE = { checkbox: 'checkbox', radio: 'radio', range: 'slider',
        search: 'searchbox', number: 'spinbutton', submit: 'button', reset: 'button',
        button: 'button', file: 'button' };

    let nodeCounter = 0;
    let stats = { total: 0, ignored: 0, interactive: 0 };
    const INTERACTIVE = new Set([
        'button','link','textbox','combobox','checkbox','radio',
        'slider','spinbutton','tab','menuitem','searchbox','switch',
        'option','menuitemcheckbox','menuitemradio','treeitem',
    ]);

    function getRole(el) {
        const explicit = el.getAttribute('role');
        if (explicit) return explicit;
        const tag = el.tagName.toLowerCase();
        if (tag === 'input') return INPUT_ROLE[el.type] || 'textbox';
        return ROLE_MAP[tag] || '';
    }

    function getName(el) {
        const label = el.getAttribute('aria-label');
        if (label) return label;
        const labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy) {
            const ref = document.getElementById(labelledBy);
            if (ref) return (ref.textContent || '').trim().slice(0, 100);
        }
        const alt = el.getAttribute('alt');
        if (alt) return alt;
        const title = el.getAttribute('title');
        if (title) return title;
        const placeholder = el.getAttribute('placeholder');
        if (placeholder) return placeholder;
        // 直接子文本
        let text = '';
        for (const child of el.childNodes) {
            if (child.nodeType === 3) text += child.textContent;
        }
        text = text.trim().slice(0, 100);
        return text;
    }

    function isHidden(el) {
        if (el.hidden || el.getAttribute('aria-hidden') === 'true') return true;
        const st = getComputedStyle(el);
        return st.display === 'none' || st.visibility === 'hidden';
    }

    function getProps(el) {
        const props = {};
        const tag = el.tagName.toLowerCase();
        if (el.hasAttribute('aria-expanded'))
            props.expanded = el.getAttribute('aria-expanded') === 'true';
        if (el.hasAttribute('aria-checked'))
            props.checked = el.getAttribute('aria-checked') === 'true';
        if (el.hasAttribute('aria-selected'))
            props.selected = el.getAttribute('aria-selected') === 'true';
        if (el.hasAttribute('aria-disabled') || el.disabled)
            props.disabled = true;
        if (el.hasAttribute('aria-required') || el.required)
            props.required = true;
        if (el.hasAttribute('aria-pressed'))
            props.pressed = el.getAttribute('aria-pressed') === 'true';
        if (/^h[1-6]$/.test(tag))
            props.level = parseInt(tag[1]);
        return props;
    }

    function buildNode(el, depth) {
        if (depth > 20) return null;
        if (el.nodeType !== 1) return null;
        if (isHidden(el)) { stats.ignored++; return null; }

        const role = getRole(el);
        const name = getName(el);
        const id = String(++nodeCounter);
        stats.total++;
        if (INTERACTIVE.has(role)) stats.interactive++;

        const children = [];
        for (const child of el.children) {
            const cn = buildNode(child, depth + 1);
            if (cn) children.push(cn);
        }

        // 跳过无意义容器（无 role、无 name、只有一个子节点）
        if (!role && !name && children.length === 1) {
            return children[0];
        }
        // 跳过完全空的无 role 节点
        if (!role && !name && children.length === 0) {
            return null;
        }

        const node = { nodeId: id, role: role || 'generic', name: name };
        const props = getProps(el);
        if (Object.keys(props).length) node.properties = props;

        const value = el.value;
        if (value !== undefined && value !== '' && role &&
            ['textbox','combobox','slider','spinbutton','searchbox'].includes(role)) {
            node.value = String(value).slice(0, 200);
        }

        // 定位器
        const loc = suggestLoc(el);
        if (loc) node.locator = loc;

        if (children.length) node.children = children;
        return node;
    }

    function suggestLoc(el) {
        const id = el.id;
        if (id) return '#' + id;
        for (const attr of ['data-testid','data-qa','aria-label','name','placeholder']) {
            const v = el.getAttribute(attr);
            if (v) return '@' + attr + '=' + v;
        }
        const cls = el.className;
        if (typeof cls === 'string' && cls.trim()) {
            return '.' + cls.trim().split(/\\s+/)[0];
        }
        const text = (el.textContent || '').trim();
        if (text && text.length <= 30) return 'text:' + text;
        return 't:' + el.tagName.toLowerCase();
    }

    const tree = buildNode(document.body, 0) || { nodeId: '0', role: 'WebArea', name: document.title };
    return { tree: tree, stats: stats };
})()
"""
