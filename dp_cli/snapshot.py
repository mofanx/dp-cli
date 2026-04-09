# -*- coding:utf-8 -*-
"""
dp-cli snapshot 模块

核心设计：两层采集，站在巨人肩膀上
  1. 可交互元素：CDP Accessibility Tree（浏览器原生过滤，角色语义完整）
  2. 主体内容：CDP JS innerText（按视觉面积×语义权重识别主体区块，不截断）

优于纯 lxml 静态解析的关键：
  - 支持 JS 动态渲染内容（SPA/Vue/React）
  - a11y tree 天然过滤不可见/装饰性节点
  - innerText 不包含 style/script 内容（浏览器原生过滤反爬注入）
  - 不依赖 lxml 版本兼容性
"""
import re

# ── 常量定义 ───────────────────────────────────────────────────────────────────

_NOISE_TAGS = {
    'script', 'style', 'noscript', 'meta', 'link',
    'svg', 'path', 'defs', 'symbol', 'use', 'g',
    'iframe', 'template', 'canvas', 'video', 'audio',
    'header', 'footer', 'nav', 'aside'
}

_CONTAINER_TAGS = {
    'html', 'body', 'div', 'section', 'article', 'main',
    'aside', 'nav', 'header', 'footer', 'ul', 'ol', 'dl',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'form', 'fieldset'
}

# ── CDP JS：一次性采集可交互元素（基于 a11y tree 映射回 DOM） ─────────────────

_JS_INTERACTIVE = r"""
return (function() {
    // 可交互的 a11y role 集合
    var INTERACTIVE_ROLES = new Set([
        'button','link','textbox','searchbox','combobox','listbox',
        'checkbox','radio','switch','slider','spinbutton','menuitem',
        'menuitemcheckbox','menuitemradio','option','tab','treeitem',
        'gridcell','columnheader','rowheader'
    ]);
    // 可交互 HTML 标签
    var INTERACTIVE_TAGS = new Set(['input','button','select','textarea','a']);
    // 噪音祖先：在这些容器内的元素降低优先级
    var NOISE_CONTAINERS = new Set(['header','footer','nav','aside']);

    // 从页面结构中提取标题，避免 document.title 被通知信息污染
    function getPageTitle() {
        var selectors = [
            'h1',
            'article h1',
            'article h2',
            '[class*="title"]',
            '.post-title',
            '.article-title',
            '[class*="Title"]',
            '.post-Title',
            '.article-Title'
        ];
        for (var sel of selectors) {
            var el = document.querySelector(sel);
            if (el && el.innerText && el.innerText.trim()) {
                var text = el.innerText.trim();
                // 过滤掉过短的文本（可能是按钮或标签）
                if (text.length >= 5 && text.length <= 100) {
                    return text;
                }
            }
        }
        // 如果找不到，回退到 document.title
        return document.title;
    }

    function isVisible(el) {
        if (!el) return false;
        var s = window.getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
        var r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }

    function inNoiseContainer(el) {
        var p = el.parentElement;
        var d = 0;
        while (p && d < 8) {
            var t = p.tagName.toLowerCase();
            if (NOISE_CONTAINERS.has(t)) return true;
            var role = p.getAttribute('role') || '';
            if (role === 'navigation' || role === 'banner' || role === 'contentinfo') return true;
            p = p.parentElement; d++;
        }
        return false;
    }

    function getCssPath(el) {
        var parts = [];
        while (el && el !== document.body && el.nodeType === 1) {
            if (el.id && /^[a-zA-Z][\w-]*$/.test(el.id)) {
                parts.unshift('#' + el.id);
                break;
            }
            var cls = Array.from(el.classList).filter(function(c){ return c.length >= 2; });
            var seg;
            if (cls.length > 0) {
                seg = '.' + cls[0];
                var sibs = el.parentElement
                    ? Array.from(el.parentElement.querySelectorAll(':scope > ' + seg)) : [];
                if (sibs.length > 1) seg += ':nth-child(' + (Array.from(el.parentElement.children).indexOf(el)+1) + ')';
            } else {
                seg = el.tagName.toLowerCase();
                var tagSibs = el.parentElement
                    ? Array.from(el.parentElement.children).filter(function(c){ return c.tagName === el.tagName; }) : [];
                if (tagSibs.length > 1) seg += ':nth-child(' + (Array.from(el.parentElement.children).indexOf(el)+1) + ')';
            }
            parts.unshift(seg);
            el = el.parentElement;
        }
        return parts.join(' > ');
    }

    function getLoc(el) {
        // 优先级1: id（最稳定）
        if (el.id && /^[a-zA-Z][\w-]*$/.test(el.id)) return '#' + el.id;

        // 优先级2: 文本（对导航按钮最通用，dp click 可直接使用）
        var t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
        if (t && t.length <= 30 && t.length >= 1) return 'text:' + t;

        // 优先级3: 测试属性
        for (var attr of ['data-testid','data-qa','data-cy','aria-label','name','placeholder']) {
            var v = el.getAttribute(attr);
            if (v && v.length <= 60) return '@' + attr + '=' + v;
        }

        // 优先级4: class
        var cls = Array.from(el.classList).filter(function(c){
            return c.length >= 2 && !/^[a-z]+-[a-z0-9]{5,}$/.test(c);
        });
        if (cls.length) return '.' + cls[0];

        // 优先级5: css path
        return 'css:' + getCssPath(el);
    }

    var results = [];
    var idx = 0;
    var seen = new Set();

    // 查找所有可交互元素
    var candidates = document.querySelectorAll(
        'input:not([type=hidden]), button, select, textarea, a, ' +
        '[role="button"],[role="link"],[role="textbox"],[role="searchbox"],' +
        '[role="combobox"],[role="checkbox"],[role="radio"],[role="tab"],' +
        '[role="menuitem"],[role="option"],[tabindex]:not([tabindex="-1"]),' +
        'label, .filter-item, .filter-option, [class*="filter"], [class*="option"]'
    );

    candidates.forEach(function(el) {
        if (!isVisible(el)) return;

        var tag = el.tagName.toLowerCase();
        var role = el.getAttribute('role') || tag;
        var text = (el.innerText || el.value || el.getAttribute('aria-label') ||
                    el.getAttribute('title') || el.getAttribute('placeholder') || '').trim().substring(0, 100);

        // 加入文本作为去重依据，防止重复的导航按钮被过滤
        var key = el.tagName + '|' + (el.id||'') + '|' + (el.className||'') + '|' + (el.getAttribute('name')||'') + '|' + text;
        if (seen.has(key)) return;
        seen.add(key);

        var inNoise = inNoiseContainer(el);

        var item = {
            idx: idx++,
            tag: tag,
            role: role,
            text: text,
            loc: getLoc(el),
            in_nav: inNoise
        };

        // 额外有用属性
        if (el.type) item.type = el.type;
        if (el.placeholder) item.placeholder = el.placeholder;
        if (tag === 'a' && el.href) item.href = el.href;
        if (el.getAttribute('aria-label')) item.aria_label = el.getAttribute('aria-label');

        results.push(item);
    });

    return {
        interactive: results,
        page_title: getPageTitle()
    };
})()
"""

# ── CDP JS：识别主体容器，遍历内部语义节点，保留层级结构 ─────────────────────

_JS_CONTENT_BLOCKS = r"""
return (function() {
    var SKIP_TAGS = new Set(['script','style','noscript','head','meta','link',
                             'svg','path','defs','symbol','use','g',
                             'iframe','template','canvas','video','audio']);
    var NOISE_TAGS = new Set(['header','footer','nav','aside']);
    var NOISE_ROLES = new Set(['navigation','banner','contentinfo','complementary',
                               'toolbar','menubar','search']);
    // 语义块级标签：遍历到这一级就输出整块文本，不再向下拆分
    var BLOCK_TAGS = new Set([
        'h1','h2','h3','h4','h5','h6',
        'p','li','dt','dd','td','th',
        'blockquote','pre','figcaption','caption',
        'summary','label','legend'
    ]);
    // 代码块单独标记
    var CODE_TAGS = new Set(['pre','code']);

    function isVisible(el) {
        var s = window.getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden') return false;
        var r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }

    function isNoise(el) {
        var tag = el.tagName.toLowerCase();
        if (NOISE_TAGS.has(tag)) return true;
        var role = el.getAttribute('role') || '';
        if (NOISE_ROLES.has(role)) return true;
        // 基于 class 的噪音识别（通用关键词）
        var cls = el.className ? el.className.toString().toLowerCase() : '';
        if (cls.indexOf('footer') !== -1 || cls.indexOf('copyright') !== -1 ||
            cls.indexOf('icp') !== -1 || cls.indexOf('beian') !== -1 ||
            cls.indexOf('boss-info') !== -1 || cls.indexOf('hot-link') !== -1 ||
            cls.indexOf('hot') !== -1 || cls.indexOf('recommend') !== -1 ||
            cls.indexOf('sidebar') !== -1 || cls.indexOf('breadcrumb') !== -1 ||
            cls.indexOf('c-breadcrumb') !== -1 || cls.indexOf('toolbar') !== -1) {
            return true;
        }
        return false;
    }

    function hasNoiseAncestor(el, root) {
        var p = el.parentElement;
        while (p && p !== root) {
            if (isNoise(p)) return true;
            p = p.parentElement;
        }
        return false;
    }

    function getCssPath(el) {
        var parts = [];
        var cur = el;
        while (cur && cur !== document.body && cur.nodeType === 1) {
            if (cur.id && /^[a-zA-Z][\w-]*$/.test(cur.id)) {
                parts.unshift('#' + cur.id); break;
            }
            var cls = Array.from(cur.classList).filter(function(c){ return c.length >= 2; });
            var seg;
            if (cls.length > 0) {
                seg = '.' + cls[0];
                var sibs = cur.parentElement
                    ? Array.from(cur.parentElement.querySelectorAll(':scope > ' + seg)) : [];
                if (sibs.length > 1)
                    seg += ':nth-child(' + (Array.from(cur.parentElement.children).indexOf(cur)+1) + ')';
            } else {
                seg = cur.tagName.toLowerCase();
                var tsib = cur.parentElement
                    ? Array.from(cur.parentElement.children).filter(function(c){ return c.tagName===cur.tagName; }) : [];
                if (tsib.length > 1)
                    seg += ':nth-child(' + (Array.from(cur.parentElement.children).indexOf(cur)+1) + ')';
            }
            parts.unshift(seg); cur = cur.parentElement;
        }
        return 'css:' + parts.join(' > ');
    }

    // 将 HTML 转换为 markdown 格式，保留超链接和图片
    function htmlToMarkdown(el, inList) {
        if (!el) return '';
        if (el.nodeType === 3) { // 文本节点
            return el.nodeValue || '';
        }
        if (el.nodeType !== 1) return '';

        var tag = el.tagName.toLowerCase();
        var result = '';

        // 处理超链接
        if (tag === 'a') {
            var href = el.getAttribute('href') || '';
            var text = '';
            for (var i = 0; i < el.childNodes.length; i++) {
                text += htmlToMarkdown(el.childNodes[i], inList);
            }
            if (href && text.trim()) {
                return '[' + text.trim() + '](' + href + ')';
            }
            return text;
        }

        // 处理图片
        if (tag === 'img') {
            var src = el.getAttribute('src') || '';
            var alt = el.getAttribute('alt') || '';
            if (src) {
                return '![alt](' + src + ')';
            }
            return '';
        }

        // 处理有序列表
        if (tag === 'ol') {
            var index = 1;
            for (var i = 0; i < el.childNodes.length; i++) {
                if (el.childNodes[i].tagName && el.childNodes[i].tagName.toLowerCase() === 'li') {
                    result += index + '. ' + htmlToMarkdown(el.childNodes[i], true).trim() + '\n';
                    index++;
                }
            }
            return result;
        }

        // 处理无序列表
        if (tag === 'ul') {
            for (var i = 0; i < el.childNodes.length; i++) {
                if (el.childNodes[i].tagName && el.childNodes[i].tagName.toLowerCase() === 'li') {
                    result += '- ' + htmlToMarkdown(el.childNodes[i], true).trim() + '\n';
                }
            }
            return result;
        }

        // 处理列表项
        if (tag === 'li') {
            for (var i = 0; i < el.childNodes.length; i++) {
                var childText = htmlToMarkdown(el.childNodes[i], true);
                // 移除子节点文本的多余空格
                result += childText.replace(/^[ \t]+/gm, '').replace(/[ \t]+/g, ' ');
            }
            return result.trim();
        }

        // 处理其他标签
        for (var i = 0; i < el.childNodes.length; i++) {
            result += htmlToMarkdown(el.childNodes[i], inList);
        }

        return result;
    }

    // ── 第一步：找最佳主体容器 ──────────────────────────────────────────────
    function findBestRoot() {
        // 优先级1：语义标签
        var byTag = document.querySelector('article, [role="article"], main, [role="main"]');
        if (byTag) return byTag;
        // 优先级2：常见 content class/id
        var patterns = [
            '[id*="content"],[id*="Content"],[id*="article"],[id*="Article"]',
            '[id*="main"],[id*="Main"],[id*="post"],[id*="Post"]',
            '[class*="article-body"],[class*="ArticleBody"],[class*="post-body"]',
            '[class*="RichText"],[class*="rich-text"],[class*="richtext"]',
            '[class*="article-content"],[class*="ArticleContent"]',
            '[class*="post-content"],[class*="PostContent"]',
            '[class*="entry-content"],[class*="content-body"]',
            '[class*="detail-content"],[class*="job-detail"]',
        ];
        var vpArea = window.innerWidth * window.innerHeight;
        for (var i = 0; i < patterns.length; i++) {
            var els = Array.from(document.querySelectorAll(patterns[i]));
            for (var j = 0; j < els.length; j++) {
                var el = els[j];
                if (!isVisible(el)) continue;
                var r = el.getBoundingClientRect();
                var area = r.width * r.height;
                // 排除太小和接近全页的容器
                if (area < 5000 || area > vpArea * 0.95) continue;
                var tlen = (el.textContent || '').replace(/\s+/g,' ').trim().length;
                if (tlen > 100) return el;
            }
        }
        // 优先级3：评分找最高密度大容器
        var vpA = window.innerWidth * window.innerHeight;
        var best = null, bestScore = 0;
        document.querySelectorAll('main, article, section, div').forEach(function(el){
            if (isNoise(el) || !isVisible(el)) return;
            var r = el.getBoundingClientRect();
            var area = r.width * r.height;
            if (area < 10000 || area > vpA * 0.9) return;
            var nc = Math.max(el.querySelectorAll('*').length, 1);
            var tl = (el.textContent||'').replace(/\s+/g,' ').trim().length;
            if (tl < 100) return;
            var score = (tl / nc) * Math.log(tl + 1);
            if (score > bestScore) { bestScore = score; best = el; }
        });
        return best || document.body;
    }

    // ── 第二步：遍历主体容器，按语义标签输出带层级的节点 ───────────────────
    function traverse(el, depth, root, results) {
        if (!el || el.nodeType !== 1) return;
        var tag = el.tagName.toLowerCase();
        if (SKIP_TAGS.has(tag)) return;
        if (isNoise(el)) return;

        // 不可见且非 pre/code（代码块可能被隐藏）
        if (!CODE_TAGS.has(tag) && !isVisible(el)) return;

        if (BLOCK_TAGS.has(tag)) {
            // 输出整块内容（克隆后移除噪音子节点）
            var clone = el.cloneNode(true);
            clone.querySelectorAll('style,script,noscript').forEach(function(n){n.remove();});
            // 移除页脚和侧边栏子节点
            clone.querySelectorAll('footer,aside').forEach(function(n){n.remove();});
            // 移除基于 class 关键词的噪音子节点
            clone.querySelectorAll('[class*="footer"],[class*="copyright"],[class*="icp"],[class*="beian"],[class*="boss-info"],[class*="hot-link"],[class*="hot"],[class*="recommend"],[class*="sidebar"],[class*="breadcrumb"],[class*="c-breadcrumb"],[class*="toolbar"]').forEach(function(n){n.remove();});
            var text = htmlToMarkdown(clone).trim();
            // 压缩多余空白，但保留换行（pre 里的保留完整格式）
            if (!CODE_TAGS.has(tag)) {
                // 清理列表前面的空格，保持列表格式
                text = text.replace(/^[ \t]+- /gm, '- ').replace(/^[ \t]+\d+\. /gm, function(m) {
                    return m.replace(/^[ \t]+/, '');
                }).replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
            }
            if (text.length > 0) {
                results.push({
                    depth: depth,
                    tag: tag,
                    text: text,
                    loc: getCssPath(el)
                });
            }
            return; // 不再递归（整块已输出）
        }

        // 容器标签：继续递归子节点
        for (var i = 0; i < el.children.length; i++) {
            var child = el.children[i];
            traverse(child, depth + 1, root, results);
        }
    }

    // ── 执行 ────────────────────────────────────────────────────────────────
    var root = findBestRoot();
    var rootTag = root.tagName.toLowerCase();
    var rootCls = root.className ? root.className.toString().trim().split(/\s+/)[0] : '';
    var rootId  = root.id || '';

    var results = [];
    for (var i = 0; i < root.children.length; i++) {
        traverse(root.children[i], 1, root, results);
    }

    return {
        root_tag: rootTag,
        root_cls: rootCls,
        root_id:  rootId,
        root_loc: getCssPath(root),
        nodes: results
    };
})()
"""


# ── 主函数 ────────────────────────────────────────────────────────────────────

def take_snapshot(page, mode: str = 'default',
                  selector: str = None,
                  max_depth: int = 12,
                  min_text: int = 2,
                  max_text: int = 2000) -> dict:
    """
    生成页面快照。

    默认模式（推荐）一次性输出：
      - 可交互元素：来自 CDP a11y tree 映射，role/loc/text 完整
      - 主体内容：JS 按视觉面积×语义权重识别 top 区块，innerText 完整

    :param mode:
        'default'     一次性输出可交互元素 + 主体内容（推荐）
        'interactive' 只输出可交互元素
        'content'     只输出主体内容区块
        'text'        页面全量纯文本
        'legacy'      旧版 auto 模式（卡片检测，向后兼容）
    :param selector: 限定范围（仅 text/legacy 模式有效）
    """
    page.wait.doc_loaded()
    page_info = {'url': page.url, 'title': page.title}

    if mode == 'text':
        root = page.s_ele(selector) if selector else page.s_ele()
        text = root.text if root else ''
        return {'page': page_info, 'mode': 'text', 'text': text}

    if mode == 'legacy':
        root = page.s_ele(selector) if selector else page.s_ele()
        if root and root.__class__.__name__ == 'NoneElement':
            return {'page': page_info, 'error': f'Selector not found: {selector}'}
        return _auto_snapshot(page, root, page_info, max_depth, min_text, max_text)

    # ── default / interactive / content：全部走 CDP ──────────────────────────
    interactive = []
    extracted_title = None

    if mode in ('default', 'interactive'):
        try:
            raw = page.run_js(_JS_INTERACTIVE)
            if isinstance(raw, dict):
                interactive = raw.get('interactive', [])
                extracted_title = raw.get('page_title')
            else:
                interactive = raw if isinstance(raw, list) else []
        except Exception as e:
            interactive = [{'error': str(e)}]

    content_data = {}
    if mode in ('default', 'content'):
        try:
            raw = page.run_js(_JS_CONTENT_BLOCKS)
            if isinstance(raw, dict):
                content_data = raw
            else:
                content_data = {'nodes': [], 'error': f'unexpected result: {type(raw)}'}
        except Exception as e:
            content_data = {'nodes': [], 'error': str(e)}

    # 使用从页面结构提取的标题（避免通知信息污染）
    if extracted_title:
        page_info['title'] = extracted_title

    return {
        'page': page_info,
        'mode': mode,
        'interactive': interactive,
        'content_data': content_data,
    }


# ── 兼容旧接口：保留旧模式函数（legacy 调用链） ───────────────────────────────

def _auto_snapshot(page, root, page_info: dict, max_depth: int,
                   min_text: int, max_text: int) -> dict:
    """
    auto 模式：自动检测页面类型并输出最有用的信息。

    检测顺序：列表页 > 内容页 > 表单页
    表单最后判断：很多页面有搜索框但主要内容是列表，不应被表单判断覆盖。
    """
    # 1. 列表页检测（最高优先级）
    cards_info = _detect_card_list(page)
    if cards_info and cards_info['count'] >= 3:
        return {
            'page': page_info,
            'mode': 'auto',
            'detected_type': 'list_page',
            **cards_info,
        }

    # 2. 内容页检测（有大段文字的详情页）
    result = _extract_content_blocks(root, max_depth=max_depth,
                                     min_text=min_text, max_text=max_text)
    if result['count'] >= 8:
        return {
            'page': page_info,
            'mode': 'auto',
            'detected_type': 'content_page',
            **result,
        }

    # 3. 表单页检测（最后判断）
    inputs = page.s_eles('xpath://*[self::input or self::select or self::textarea]')
    visible_inputs = [e for e in inputs
                      if e.attrs.get('type', '') not in ('hidden', 'submit', 'button')]
    if len(visible_inputs) >= 2:
        elements = _extract_controls(page)  # 只输出表单控件，不包括链接
        return {
            'page': page_info,
            'mode': 'auto',
            'detected_type': 'form_page',
            'hint': '检测到表单页。使用 fill/select/click 操作表单元素。',
            'count': len(elements),
            'elements': elements,
        }

    # 4. 默认：输出 content（即使节点少）
    return {
        'page': page_info,
        'mode': 'auto',
        'detected_type': 'content_page',
        **result,
    }


def _detect_card_list(page) -> dict:
    """
    检测页面是否存在重复卡片结构（列表页）。
    两阶段策略：
    1. 静态 lxml（s_eles）快速扫描 HTML 源码中的重复元素
    2. 动态 CDP（eles）处理 JS 动态渲染内容（第一阶段未命中时）
    全程纯内容评分，不依赖类名关键词过滤。
    """
    # ── 第一阶段：静态 lxml 扫描 ─────────────────────────────────
    all_els = page.s_eles(
        'xpath://div[@class] | xpath://article[@class] '
        '| xpath://li[@class] | xpath://section[@class] '
        '| xpath://tr[@class]'
    )
    class_count: dict = {}
    for ele in all_els:
        cls = (ele.attrs.get('class') or '').strip()
        if not cls or len(cls) > 200:
            continue
        first_cls = cls.split()[0]
        if not first_cls or len(first_cls) < 3:
            continue
        class_count[first_cls] = class_count.get(first_cls, 0) + 1

    # 只对高频 class 评分，忽略频次过低的
    static_iter = []
    for first_cls, cnt in sorted(class_count.items(), key=lambda x: -x[1]):
        if cnt < 3:
            break
        try:
            items = page.s_eles(f'css:.{first_cls}')
            if len(items) >= 3:
                static_iter.append((first_cls, cnt, list(items)))
        except Exception:
            pass

    static_candidates = _score_card_candidates(static_iter)
    for score, items, sel, cls in static_candidates:
        if score < 8:
            break
        result = _build_card_result(items, sel, container_class=cls, use_cdp=False)
        if result:
            return result

    # ── 第二阶段： CDP 动态 DOM（应对 JS 动态渲染） ──────────────────
    # 用 JS 计数重复 class，需要走 CDP，技能费时更多但能命中动态内容
    try:
        js_result = page.run_js("""
            const freq = {};
            const els = document.querySelectorAll('[class]');
            for (const el of els) {
                const tag = el.tagName.toLowerCase();
                if (!['div','article','li','section','tr','a'].includes(tag)) continue;
                const first = el.className.trim().split(/\\s+/)[0];
                if (first && first.length >= 3 && first.length <= 60)
                    freq[first] = (freq[first] || 0) + 1;
            }
            return Object.entries(freq)
                .filter(([k,v]) => v >= 3)
                .sort((a,b) => b[1]-a[1])
                .slice(0, 30)
                .map(([k,v]) => ({cls: k, cnt: v}));
        """)
    except Exception:
        js_result = []

    if js_result:
        cdp_iter = []
        for item in (js_result or []):
            try:
                cls = item['cls']
                cnt = item['cnt']
                items = page.eles(f'css:.{cls}')
                if len(items) >= 3:
                    cdp_iter.append((cls, cnt, list(items)))
            except Exception:
                pass

        cdp_candidates = _score_card_candidates(cdp_iter, use_cdp=True)
        for score, items, sel, cls in cdp_candidates:
            if score < 8:
                break
            result = _build_card_result(items, sel, container_class=cls, use_cdp=True)
            if result:
                return result

    # ── 第三阶段：无 class 行结构检测（极简 HTML，如 HN）─────────────────────
    # 针对使用 table/tr 或裸 li 的页面，通过文本行密度判断
    try:
        row_candidates = []
        for sel, min_count in [
            ('xpath://table//tr[.//a]', 5),    # 带链接的 table 行
            ('xpath://ol/li[.//a]', 5),         # 有序列表带链接
        ]:
            rows = page.s_eles(sel)
            if len(rows) >= min_count:
                sample = list(rows)[:12]
                texts = [(r.text or '').strip() for r in sample]
                avg_len = sum(len(t) for t in texts) / max(len(texts), 1)
                if avg_len >= 20:  # 行内容丰富度足够
                    row_candidates.append((avg_len, list(rows), sel))

        if row_candidates:
            row_candidates.sort(key=lambda x: -x[0])
            best_avg, best_rows, best_sel = row_candidates[0]
            result = _build_card_result(best_rows, best_sel, use_cdp=False)
            if result:
                return result
    except Exception:
        pass

    return None


def _score_card_candidates(items_iter, use_cdp=False) -> list:
    """
    核心评分函数：统一对静态/动态元素列表进行内容丰富度评分。
    items_iter: list of (first_cls, cnt, items)
    返回: sorted list of (score, items, sel, cls)
    """
    candidates = []
    for first_cls, cnt, items in items_iter:
        if len(items) < 3:
            continue
        sample = list(items)[:12]
        texts = []
        for it in sample:
            try:
                texts.append((it.text or '').strip())
            except Exception:
                texts.append('')

        avg_len = sum(len(t) for t in texts) / max(len(texts), 1)
        multiline_ratio = sum(1 for t in texts if '\n' in t) / max(len(texts), 1)
        unique_ratio = len(set(texts)) / max(len(texts), 1)
        count_weight = min(cnt, 50) / 50.0

        # 子元素层次权重：优先选高层容器而非单字段节点
        try:
            child_count = len(list(sample[0].children()))
        except Exception:
            child_count = 0
        depth_weight = min(child_count / 3, 2.0)

        score = ((avg_len * 0.5 + avg_len * multiline_ratio * 2.0)
                 * unique_ratio * count_weight * max(depth_weight, 0.5))
        candidates.append((score, list(items), f'css:.{first_cls}', first_cls))

    candidates.sort(key=lambda x: -x[0])
    return candidates


def _build_card_result(items, selector: str, container_class: str = None,
                       use_cdp: bool = False) -> dict:
    """从卡片列表中提取前5条内容，并给出 extract 工作流提示"""
    sample_cards = []
    for item in list(items)[:5]:
        card = _extract_card_fields(item, use_cdp=use_cdp)
        if card:
            sample_cards.append(card)

    if not sample_cards:
        return None

    # 质量检查：多张卡片之间需要有足够的字段交集，否则不是真正的列表页
    if len(sample_cards) >= 2:
        non_link_keys = [set(k for k in c if not k.startswith('_')) for c in sample_cards]
        common = non_link_keys[0].copy()
        for s in non_link_keys[1:]:
            common &= s
        # 有效列表页：至少1个公共字段，或者总字段交集比例 > 30%
        total_unique = len(set().union(*non_link_keys))
        if not common and total_unique > 0:
            return None  # 字段完全不同，说明不是结构化列表

    if use_cdp:
        field_hints = _infer_fields_from_sample_cards(sample_cards)
    else:
        field_hints = _infer_field_selectors(list(items)[:3], use_cdp=False)

    container_sel = f'css:.{container_class}' if container_class else selector

    result = {
        'count': len(items),
        'container_selector': container_sel,
        'sample_cards': sample_cards,
        'hint': (
            f'检测到列表页，共 {len(items)} 个卡片（{container_sel}）。\n'
            f'建议使用以下命令批量提取所有数据：\n'
            f'  dp extract "{container_sel}" \'{_format_fields_json(field_hints)}\''
        ),
        'suggested_fields': field_hints,
    }
    return result


def _extract_card_fields(card_ele, use_cdp: bool = False) -> dict:
    """从单张卡片元素提取所有有意义的文本/链接字段。
    use_cdp=True 时使用 CDP eles 查询子元素，并用 JS 提取字段。
    """
    result = {}
    try:
        if use_cdp:
            # CDP 元素用 JS 直接提取所有带 class 的子元素文本
            # 这比递归 children() 更可靠
            try:
                fields_js = card_ele.run_js("""
                    const result = {};
                    const all = this.querySelectorAll('[class]');
                    let idx = 0;
                    for (const el of all) {
                        const tag = el.tagName.toLowerCase();
                        if (['script','style','svg'].includes(tag)) continue;
                        const text = el.innerText ? el.innerText.trim() : '';
                        if (!text || text.length < 2 || text.length > 300) continue;
                        // 只要叶子元素（没有包含文本的子元素）
                        const childTexts = Array.from(el.children)
                            .map(c => c.innerText ? c.innerText.trim() : '');
                        const isLeaf = !childTexts.some(t => t.length > 1);
                        if (!isLeaf) continue;
                        const cls = el.className.trim().split(/\\s+/)[0];
                        const key = cls || 'field_' + idx;
                        if (!result[key]) result[key] = text;
                        idx++;
                        if (idx > 20) break;
                    }
                    return result;
                """)
                if isinstance(fields_js, dict):
                    result.update(fields_js)
            except Exception:
                pass

            # 收集链接
            try:
                links = card_ele.eles('css:a', timeout=0)
                for a in list(links)[:3]:
                    href = (a.attrs.get('href') or '')
                    link_text = (a.text or '').strip()
                    if href and not href.startswith('javascript'):
                        result[f'_link_{link_text[:15] or "link"}'] = href
            except Exception:
                pass
        else:
            # 静态元素用递归 children()
            leaf_texts = _collect_leaf_texts(card_ele, max_items=20)
            for i, (text, tag, cls) in enumerate(leaf_texts):
                key = cls or f'field_{i}'
                if key in result:
                    key = f'{key}_{i}'
                result[key] = text
            try:
                links = card_ele.s_eles('css:a')
                for a in list(links)[:3]:
                    href = (a.attrs.get('href') or '')
                    link_text = (a.text or '').strip()
                    if href and not href.startswith('javascript'):
                        result[f'_link_{link_text[:15] or "link"}'] = href
            except Exception:
                pass
    except Exception:
        pass
    return result


def _collect_leaf_texts(ele, depth: int = 0, max_depth: int = 8,
                        max_items: int = 20) -> list:
    """递归收集元素内所有有文字的叶子节点，返回 (text, tag, first_class) 列表"""
    results = []
    if depth > max_depth or len(results) >= max_items:
        return results

    try:
        tag = (ele.tag or '').lower()
    except Exception:
        return results

    if tag in _NOISE_TAGS:
        return results

    try:
        attrs = ele.attrs
    except Exception:
        attrs = {}

    try:
        children = list(ele.children())
    except Exception:
        children = []

    # 叶子节点或没有有意义子节点时取自身文本
    if not children or tag in ('span', 'a', 'em', 'strong', 'b', 'i', 'time',
                                'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p',
                                'td', 'th', 'li', 'dt', 'dd', 'label',
                                'button'):
        try:
            text = (ele.text or '').strip()
        except Exception:
            text = ''
        if text and 2 <= len(text) <= 300 and not _is_noise_text(text):
            cls = (attrs.get('class') or '').split()[0] if attrs.get('class') else ''
            results.append((text, tag, cls))
        return results

    # 容器节点递归
    for child in children:
        results.extend(_collect_leaf_texts(child, depth + 1, max_depth, max_items))
        if len(results) >= max_items:
            break
    return results


def _infer_field_selectors(items: list, use_cdp: bool = False) -> dict:
    """
    分析前几张卡片，推断各字段的最优 CSS 选择器。
    use_cdp=True 时使用 CDP eles 查询，适合动态内容。
    """
    if not items:
        return {}

    all_classes = []
    for item in items:
        card_classes = _get_leaf_classes(item)
        all_classes.append(card_classes)

    if not all_classes:
        return {}

    common = set(all_classes[0])
    for s in all_classes[1:]:
        common &= set(s)

    fields = {}
    first_item = items[0]
    for cls in list(common)[:8]:
        try:
            if use_cdp:
                sample_ele = first_item.ele(f'css:.{cls}', timeout=0)
            else:
                sample_ele = first_item.s_ele(f'css:.{cls}')
            if sample_ele and sample_ele.__class__.__name__ != 'NoneElement':
                sample_text = (sample_ele.text or '').strip()[:60]
                if sample_text and not _is_noise_text(sample_text):
                    fields[cls] = f'css:.{cls}'
        except Exception:
            pass

    return fields


def _get_leaf_classes(ele, depth: int = 0, max_depth: int = 6) -> list:
    """收集元素下所有叶子节点的第一个 class 名。
    兼容静态(s_ele)和动态(ele/CDP)元素。
    """
    results = []
    if depth > max_depth:
        return results
    try:
        tag = (ele.tag or '').lower()
        if tag in _NOISE_TAGS:
            return results
        attrs = ele.attrs
        cls = (attrs.get('class') or '').split()
        first_cls = cls[0] if cls else ''

        # 尝试获取子元素（静态元素用 children()，动态元素可能没有这个方法）
        children = []
        if hasattr(ele, 'children'):
            try:
                children = list(ele.children())
            except Exception:
                pass

        is_leaf = not children or tag in ('span', 'a', 'em', 'strong', 'b', 'i',
                                           'time', 'p', 'td', 'th', 'li', 'button')
        if is_leaf and first_cls and len(first_cls) >= 3:
            text = (ele.text or '').strip()
            if text and 2 <= len(text) <= 200:
                results.append(first_cls)
        elif children:
            for child in children:
                results.extend(_get_leaf_classes(child, depth + 1, max_depth))
        else:
            # CDP 元素没有 children() 方法，尝试用 s_eles 获取子元素
            try:
                sub_eles = ele.eles('xpath://*[@class]', timeout=0)
                for sub in list(sub_eles)[:20]:
                    sub_cls = (sub.attrs.get('class') or '').split()
                    sub_first = sub_cls[0] if sub_cls else ''
                    if sub_first and len(sub_first) >= 3:
                        sub_text = (sub.text or '').strip()
                        if sub_text and 2 <= len(sub_text) <= 200:
                            results.append(sub_first)
            except Exception:
                pass
    except Exception:
        pass
    return results


def _infer_fields_from_sample_cards(sample_cards: list) -> dict:
    """
    CDP 模式专用：从 sample_cards 的 key（已是 class 名）直接生成字段选择器。
    取所有卡片都有的字段（交集），过滤链接字段（以 _link_ 开头）。
    """
    if not sample_cards:
        return {}
    # 取非链接字段的交集
    common_keys = set(k for k in sample_cards[0] if not k.startswith('_'))
    for card in sample_cards[1:]:
        common_keys &= set(k for k in card if not k.startswith('_'))
    # class 名即选择器
    return {k: f'css:.{k}' for k in list(common_keys)[:8]
            if len(k) >= 3 and not k.startswith('field_')}


def _format_fields_json(fields: dict) -> str:
    """将字段字典格式化为 extract 命令的 JSON 字符串"""
    import json
    simple = {name: sel for name, sel in fields.items()}
    return json.dumps(simple, ensure_ascii=False)


# ── Content 模式（重新设计）─────────────────────────────────────────────────

def _extract_content_blocks(root, max_depth: int = 12,
                            min_text: int = 2, max_text: int = 2000) -> dict:
    """
    content 模式（重新设计版）：
    - 不再在第一个有文本节点处截止，而是完整遍历 DOM
    - 区分「文本叶子」和「区块容器」
    - 叶子节点：直接输出 tag/text/loc/class
    - 区块容器（div/section/article/ul/ol）：输出区块标识 + 其下所有叶子文本
    - 自动跳过噪音（script/style/空容器/超短文本）
    返回 { nodes: [...], hint: '...' }
    """
    nodes = []
    seen_texts = set()  # 去重，避免父子节点重复输出同一段文本

    def _walk(ele, depth, in_block=False):
        if depth > max_depth:
            return
        try:
            tag = (ele.tag or '').lower()
        except Exception:
            return
        if tag in _NOISE_TAGS:
            return

        try:
            attrs = ele.attrs
        except Exception:
            attrs = {}

        # 过滤内嵌 CSS/数据
        if tag == 'textarea':
            t = attrs.get('type', '')
            if 'css' in t or 'json' in t:
                return

        try:
            children = list(ele.children())
        except Exception:
            children = []

        # 当前节点文本：合并子节点文本（处理反爬 span 拆分），剔除 style/script
        raw_text = _clean_ele_text(ele)

        is_leaf = (not children
                   or tag in ('span', 'a', 'em', 'strong', 'b', 'i', 'time',
                              'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                              'p', 'td', 'th', 'li', 'dt', 'dd',
                              'label', 'button', 'option'))

        if is_leaf:
            text = raw_text
            if (text and min_text <= len(text) <= max_text
                    and not _is_noise_text(text)
                    and text not in seen_texts):
                seen_texts.add(text)
                first_cls = (attrs.get('class') or '').split()[0] if attrs.get('class') else ''
                node = {
                    'tag': tag,
                    'text': text[:1500],
                    'loc': _suggest_locator_static(tag, attrs, text),
                }
                if first_cls:
                    node['class'] = first_cls
                nodes.append(node)
            return

        # 容器节点：检查是否是有意义的区块
        cls_str = (attrs.get('class') or '').strip()
        ele_id = attrs.get('id', '')
        is_semantic = (tag in ('article', 'section', 'header', 'footer',
                               'nav', 'aside', 'main', 'ul', 'ol', 'table',
                               'form', 'fieldset', 'dl')
                       or bool(ele_id))

        # 输出区块分隔标记（只对有语义意义的容器）
        if is_semantic and depth <= 6:
            block_label = ele_id or cls_str.split()[0] if cls_str else tag
            if block_label and block_label != tag:
                nodes.append({
                    'tag': tag,
                    'text': f'[{tag}#{ele_id or block_label}]',
                    'loc': f'#{ele_id}' if ele_id else f'css:.{cls_str.split()[0]}',
                    'class': cls_str.split()[0] if cls_str else '',
                    '_is_block': True,
                })

        for child in children:
            _walk(child, depth + 1, in_block or is_semantic)

    _walk(root, 0)

    # 过滤掉只有 _is_block 且后面没有内容的孤立分隔符
    cleaned = []
    for i, n in enumerate(nodes):
        if n.get('_is_block'):
            # 只保留后面紧跟有内容节点的区块标记
            has_content_after = any(
                not nodes[j].get('_is_block')
                for j in range(i + 1, min(i + 4, len(nodes)))
            )
            if has_content_after:
                del n['_is_block']
                cleaned.append(n)
        else:
            cleaned.append(n)

    hint = ''
    if len(cleaned) < 5:
        hint = ('内容节点较少，页面可能需要登录或为动态加载。'
                '建议先 dp wait --loaded，或尝试 dp snapshot --mode text 查看原始文本。')

    return {
        'count': len(cleaned),
        'nodes': cleaned,
        'hint': hint,
    }


def _extract_content_nodes(root, max_depth: int = 12,
                           min_text: int = 2, max_text: int = 2000) -> list:
    """兼容旧调用，内部调用新版 _extract_content_blocks"""
    return _extract_content_blocks(root, max_depth, min_text, max_text)['nodes']


def _is_noise_text(text: str) -> bool:
    """判断是否是无意义的噪音文本（纯空白、纯数字符号、超短无语义）"""
    t = text.strip()
    if not t or len(t) < 2:
        return True
    # 全是标点/符号
    if re.match(r'^[\s\W]+$', t):
        return True
    return False


def extract_structured(page, container: str, fields: dict,
                       limit: int = 100) -> list:
    """
    结构化批量提取——核心数据提取原语。

    在页面上找到所有 container 匹配的元素（每个视为一条记录），
    然后在每个容器内按 fields 字典提取各字段值。

    :param page: ChromiumPage
    :param container: 容器定位器，如 'css:.job-card' 或 'xpath://li[@class="item"]'
    :param fields: 字段映射字典，如
        {
          "title":  "css:.job-name",
          "salary": "css:.salary",
          "company": "css:.company-name",
          "tags":   {"selector": "css:.tag", "multi": True},
          "url":    {"selector": "css:a", "attr": "href"},
        }
        值可以是：
        - 字符串：子元素定位器，取 text
        - dict with 'selector': 子元素定位器
          - 'multi': True → 返回文本列表
          - 'attr': 'href' → 取属性值而非 text
          - 'default': 缺失时的默认值
    :param limit: 最多提取多少条记录
    :return: list of dict
    """
    containers = page.s_eles(container)
    if not containers:
        return []

    results = []
    for item in list(containers)[:limit]:
        record = {}
        for field_name, spec in fields.items():
            # 规范化 spec
            if isinstance(spec, str):
                spec = {'selector': spec}

            sel = spec.get('selector', '')
            multi = spec.get('multi', False)
            attr = spec.get('attr', None)
            default = spec.get('default', '')

            try:
                if multi:
                    eles = item.s_eles(sel)
                    record[field_name] = [
                        (e.attr(attr) if attr else (e.text or '').strip())
                        for e in eles
                    ]
                else:
                    ele = item.s_ele(sel)
                    if ele and ele.__class__.__name__ != 'NoneElement':
                        if attr:
                            record[field_name] = ele.attr(attr) or default
                        else:
                            record[field_name] = (ele.text or '').strip() or default
                    else:
                        record[field_name] = default
            except Exception:
                record[field_name] = default

        results.append(record)

    return results


_JS_CSS_PATH = """
var el = this;
var parts = [];
while (el && el !== document.body && el.nodeType === 1) {
    var seg = el.tagName.toLowerCase();
    if (el.id && /^[a-zA-Z][\\w-]*$/.test(el.id)) {
        parts.unshift('#' + el.id);
        break;
    }
    var classes = Array.from(el.classList)
        .filter(function(c) { return c.length >= 3; });
    if (classes.length > 0) {
        seg = '.' + classes[0];
        var siblings = el.parentElement
            ? Array.from(el.parentElement.querySelectorAll(':scope > ' + seg))
            : [];
        if (siblings.length > 1) {
            var idx = siblings.indexOf(el) + 1;
            seg = seg + ':nth-child(' + idx + ')';
        }
    } else {
        var allSiblings = el.parentElement
            ? Array.from(el.parentElement.children).filter(function(c) { return c.tagName === el.tagName; })
            : [];
        if (allSiblings.length > 1) {
            var idx2 = Array.from(el.parentElement.children).indexOf(el) + 1;
            seg = seg + ':nth-child(' + idx2 + ')';
        }
    }
    parts.unshift(seg);
    el = el.parentElement;
}
return parts.join(' > ');
"""

_JS_XPATH = """
var el = this;
var parts = [];
while (el && el.nodeType === 1) {
    var seg = el.tagName.toLowerCase();
    if (el.id && /^[a-zA-Z][\\w-]*$/.test(el.id)) {
        parts.unshift('//' + seg + '[@id="' + el.id + '"]');
        return parts.join('/');
    }
    var siblings = el.parentElement
        ? Array.from(el.parentElement.children).filter(function(c) { return c.tagName === el.tagName; })
        : [];
    if (siblings.length > 1) {
        var idx = siblings.indexOf(el) + 1;
        seg = seg + '[' + idx + ']';
    }
    parts.unshift(seg);
    el = el.parentElement;
}
return '/' + parts.join('/');
"""


def query_elements(page, selector: str, fields: list,
                   limit: int = 200) -> list:
    """
    query 模式：找到所有匹配 selector 的元素，批量提取指定属性/文本。

    :param page: ChromiumPage
    :param selector: 元素定位器
    :param fields: 要提取的字段列表，支持：
                   text      → 文本内容
                   tag       → 标签名
                   loc       → 推荐 DrissionPage 定位器（简短，可能不唯一）
                   css_path  → 精确 CSS 路径（JS生成，从祖先到当前元素，唯一）
                   xpath     → 精确 XPath（JS生成）
                   其他      → HTML 属性值（href/id/class/src 等）
    :param limit: 最多返回多少条
    :return: list of dict
    """
    need_cdp = any(f in fields for f in ('css_path', 'xpath'))

    # 优先用 CDP eles（支持动态渲染内容）；静态回退用 s_eles
    try:
        eles = page.eles(selector, timeout=5)
    except Exception:
        eles = page.s_eles(selector)

    results = []
    for ele in list(eles)[:limit]:
        record = {}
        for f in fields:
            try:
                if f == 'text':
                    record['text'] = (ele.text or '').strip()
                elif f == 'tag':
                    record['tag'] = ele.tag
                elif f == 'loc':
                    record['loc'] = _suggest_locator_static(
                        ele.tag, ele.attrs, (ele.text or '').strip()[:50]
                    )
                elif f == 'css_path':
                    # JS 生成精确 CSS 路径，输出带 css: 前缀可直接用于 dp query/click
                    try:
                        path = ele.run_js(_JS_CSS_PATH)
                        record['css_path'] = f'css:{path}' if path else ''
                    except Exception:
                        record['css_path'] = ''
                elif f == 'xpath':
                    try:
                        path = ele.run_js(_JS_XPATH)
                        record['xpath'] = f'xpath:{path}' if path else ''
                    except Exception:
                        record['xpath'] = ''
                else:
                    val = ele.attrs.get(f, '') if hasattr(ele, 'attrs') else ''
                    record[f] = val or ''
            except Exception:
                record[f] = ''
        results.append(record)
    return results


def _build_tree(ele, depth: int, max_depth: int) -> dict:
    """递归构建 DOM 树（s_ele 静态解析，高效）"""
    if ele is None:
        return {}
    try:
        tag = ele.tag.lower()
    except Exception:
        return {}

    node = {
        'tag': tag,
        'attrs': _filter_attrs(ele.attrs),
        'text': (ele.raw_text or '').strip()[:100],
    }

    if depth < max_depth and tag not in _CONTAINER_TAGS:
        children = []
        try:
            for child in ele.children():
                child_node = _build_tree(child, depth + 1, max_depth)
                if child_node:
                    children.append(child_node)
        except Exception:
            pass
        if children:
            node['children'] = children

    return node


def _filter_attrs(attrs: dict) -> dict:
    """过滤掉过长或无意义的属性"""
    result = {}
    skip = {'style', 'srcset', 'sizes', 'integrity', 'crossorigin'}
    for k, v in attrs.items():
        if k in skip:
            continue
        if isinstance(v, str) and len(v) > 200:
            v = v[:200] + '...'
        result[k] = v
    return result


def _suggest_locator_static(tag: str, attrs: dict, text: str) -> str:
    """为静态元素生成最优 DrissionPage 定位字符串"""
    if attrs.get('id'):
        return f'#{attrs["id"]}'

    for semantic in ('data-testid', 'data-qa', 'data-cy', 'aria-label', 'name', 'placeholder'):
        if attrs.get(semantic):
            val = attrs[semantic]
            return f'@{semantic}={val}'

    cls = attrs.get('class', '')
    if cls:
        classes = [c for c in cls.strip().split() if not re.match(r'^[a-z]+-\w{4,}$', c)]
        if classes:
            return f'.{classes[0]}'

    if text and len(text) <= 30:
        return f'text:{text}'

    return f't:{tag}'



def render_snapshot_text(snapshot: dict) -> str:
    """
    将快照数据渲染为人类/AI 可读文本。
    新格式：可交互元素 + 主体内容块，一目了然。
    """
    lines = []
    page_info = snapshot.get('page', {})
    lines.append("### Page")
    lines.append(f"- URL: {page_info.get('url', '')}")
    lines.append(f"- Title: {page_info.get('title', '')}")
    lines.append('')

    mode = snapshot.get('mode', 'default')

    # ── 新格式：default / interactive / content ──────────────────────────────
    if mode in ('default', 'interactive', 'content'):

        interactive = snapshot.get('interactive', [])
        content_blocks = snapshot.get('content_blocks', [])

        if interactive:
            # 分组：主体交互 vs 导航交互
            main_els = [e for e in interactive if not e.get('in_nav')]
            nav_els  = [e for e in interactive if e.get('in_nav')]

            lines.append(f"### 可交互元素 ({len(interactive)} 个)")
            lines.append('')
            lines.append("#### 主体操作区")
            if main_els:
                for e in main_els:
                    _render_interactive_item(e, lines)
            else:
                lines.append("  （无）")
            lines.append('')

            if nav_els:
                lines.append(f"#### 导航/工具栏 ({len(nav_els)} 个)")
                for e in nav_els:
                    _render_interactive_item(e, lines)
                lines.append('')

        content_data = snapshot.get('content_data', {})
        nodes = content_data.get('nodes', [])
        if nodes:
            root_loc = content_data.get('root_loc', '')
            root_label = (content_data.get('root_id') or
                          content_data.get('root_cls') or
                          content_data.get('root_tag', ''))
            lines.append(f"### 主体内容  [{root_label}]  loc: {root_loc}")
            lines.append('')
            for node in nodes:
                tag  = node.get('tag', 'p')
                text = node.get('text', '')
                depth = node.get('depth', 1)
                loc  = node.get('loc', '')
                if not text:
                    continue
                # 标题：用 Markdown # 表示
                if tag in ('h1','h2','h3','h4','h5','h6'):
                    level = int(tag[1])
                    lines.append(f"{'#' * level} {text}")
                # 代码块：用 ``` 包裹
                elif tag == 'pre':
                    lines.append('```')
                    lines.append(text)
                    lines.append('```')
                # 列表项：用 - 前缀
                elif tag == 'li':
                    lines.append(f"- {text}")
                # 块引用
                elif tag == 'blockquote':
                    for bline in text.split('\n'):
                        lines.append(f"> {bline}")
                # 普通段落和其他块级元素
                else:
                    lines.append(text)
                lines.append('')

        if not interactive and not nodes:
            lines.append("（快照为空，页面可能未加载完成，请先 dp wait --loaded）")
        if content_data.get('error'):
            lines.append(f"⚠ 内容提取警告: {content_data['error']}")

    # ── text 模式 ────────────────────────────────────────────────────────────
    elif mode == 'text':
        lines.append("### Page Text")
        lines.append('')
        lines.append(snapshot.get('text', ''))

    # ── legacy / auto 模式（向后兼容旧渲染逻辑） ─────────────────────────────
    else:
        _render_legacy(snapshot, lines)

    if 'error' in snapshot:
        lines.append(f"\n### Error")
        lines.append(snapshot['error'])

    return '\n'.join(lines)


def _render_interactive_item(e: dict, lines: list) -> None:
    tag  = e.get('tag', '')
    role = e.get('role', tag)
    text = e.get('text', '')
    loc  = e.get('loc', '')
    tp   = e.get('type', '')
    ph   = e.get('placeholder', '')
    href = e.get('href', '')
    aria = e.get('aria_label', '')

    parts = [f"[{e.get('idx','')}]", f"<{tag}>"]
    if role and role != tag:
        parts.append(f"role={role}")
    if tp and tp not in ('submit','button','text'):
        parts.append(f"type={tp}")
    if text:
        parts.append(f'"{text[:60]}"')
    if ph:
        parts.append(f'placeholder="{ph[:40]}"')
    if aria and aria != text:
        parts.append(f'aria="{aria[:40]}"')
    if href:
        parts.append(f'href="{href[:60]}"')

    lines.append('  ' + ' '.join(parts))
    lines.append(f'     → loc: {loc}')


def _render_legacy(snapshot: dict, lines: list) -> None:
    """向后兼容：渲染旧版 auto/interactive/content/full 格式"""
    mode = snapshot.get('mode', '')
    detected_type = snapshot.get('detected_type', '')

    hint = snapshot.get('hint', '')
    if hint:
        lines.append("### Hint")
        lines.extend(hint.split('\n'))
        lines.append('')

    if 'sample_cards' in snapshot:
        lines.append("### Detected: List Page")
        lines.append(f"- Container: {snapshot.get('container_selector', '')}")
        lines.append(f"- Total cards: {snapshot.get('count', 0)}")
        lines.append('')
        lines.append("#### Sample Cards (first 5)")
        for i, card in enumerate(snapshot.get('sample_cards', [])):
            lines.append(f"--- Card {i+1} ---")
            for k, v in card.items():
                if not k.startswith('_'):
                    lines.append(f"  {k}: {str(v)[:80]}")
        lines.append('')
        lines.append("#### Suggested Fields for extract")
        for fname, fsel in snapshot.get('suggested_fields', {}).items():
            sample_val = snapshot.get('sample_cards', [{}])[0].get(fname, '')
            lines.append(f"  {fname:20s} → {fsel}   # e.g. {str(sample_val)[:40]}")

    elif 'elements' in snapshot:
        label = 'Form Elements' if detected_type == 'form_page' else 'Interactive Elements'
        lines.append(f"### {label} ({snapshot.get('count', 0)} found)")
        lines.append('')
        for ele in snapshot.get('elements', []):
            idx  = ele['idx']
            tag  = ele['tag']
            text = ele.get('text', '')
            loc  = ele.get('loc', '')
            attrs = ele.get('attrs', {})
            hidden = ele.get('hidden', False)
            attr_parts = []
            for k in ('type','name','placeholder','href','value','aria-label','id','class'):
                if k in attrs and len(str(attrs[k])) <= 50:
                    attr_parts.append(f'{k}="{attrs[k]}"')
            attr_str = ' '.join(attr_parts)
            text_str = f' "{text}"' if text else ''
            hidden_str = ' [hidden]' if hidden else ''
            lines.append(f"[{idx}] <{tag}{(' '+attr_str) if attr_str else ''}>{text_str}{hidden_str}")
            lines.append(f"     loc: {loc}")

    elif 'nodes' in snapshot:
        nodes = snapshot.get('nodes', [])
        lines.append(f"### Content Nodes ({len(nodes)} found)")
        lines.append('')
        for node in nodes:
            tag  = node.get('tag', '')
            text = node.get('text', '')
            loc  = node.get('loc', '')
            cls  = node.get('class', '')
            if tag in ('h1','h2','h3','h4','h5','h6'):
                lines.append('#' * int(tag[1]) + ' ' + text)
            elif text.startswith('[') and text.endswith(']'):
                lines.append(f"\n──── {text} ────")
            else:
                lines.append(f"- {text}{'  .' + cls if cls else ''}")
            if loc and loc not in (f't:{tag}', ''):
                lines.append(f"  → loc: {loc}")

    elif mode == 'full':
        lines.append("### DOM Tree")
        lines.append('')
        _render_tree_lines(snapshot.get('tree', {}), lines, 0)


def _render_tree_lines(node: dict, lines: list, indent: int) -> None:
    if not node:
        return
    tag    = node.get('tag', '')
    attrs  = node.get('attrs', {})
    text   = node.get('text', '').strip()
    children = node.get('children', [])
    attr_parts = []
    for k in ('id','class','type','name','href','placeholder'):
        if k in attrs:
            attr_parts.append(f'{k}="{attrs[k]}"')
    attr_str = (' ' + ' '.join(attr_parts)) if attr_parts else ''
    text_str = f' "{text[:50]}"' if text else ''
    lines.append('  ' * indent + f"<{tag}{attr_str}>{text_str}")
    for child in children:
        _render_tree_lines(child, lines, indent + 1)

