# -*- coding:utf-8 -*-
"""
所有注入浏览器的 JS 脚本。

三大核心脚本：
  1. _JS_PAGE_STRUCTURE — 页面结构分析（识别模块 + 重复模式 + 交互元素按区域分组）
  2. _JS_CONTENT_BLOCKS — 主体内容提取（评分公式识别最佳内容区块，输出 markdown）
  3. _JS_INTERACTIVE — 扁平可交互元素列表（兼容旧模式）
"""

# ── JS：页面结构分析（NEW — 核心） ─────────────────────────────────────────────
#
# 输出格式：
# {
#   sections: [
#     {
#       id: 'header', cls: 'header-v2', tag: 'div',
#       type: 'header',                       // header|nav|search|list|content|footer|sidebar|main|other
#       label: '导航栏',                       // 自动推断的中文标签
#       elements: [{tag, role, text, loc}, ...],
#       pattern: {selector, count, sampleFields: [...]},  // 重复模式检测
#       textLen: 61, interactiveCount: 27,
#       children: [...]                        // 嵌套子区域
#     },
#     ...
#   ]
# }

_JS_PAGE_STRUCTURE = r"""
return (function() {
    var SKIP = new Set(['script','style','noscript','svg','iframe','template','canvas','video','audio']);

    // ── 可见性检测 ──
    function isVisible(el) {
        if (!el) return false;
        var s = window.getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
        var r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }

    // ── 定位器生成 ──
    function getLoc(el) {
        if (el.id && /^[a-zA-Z][\w-]*$/.test(el.id)) return '#' + el.id;
        var t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
        if (t && t.length <= 30 && t.length >= 1) return 'text:' + t;
        for (var a of ['data-testid','data-qa','data-cy','aria-label','name','placeholder']) {
            var v = el.getAttribute(a);
            if (v && v.length <= 60) return '@' + a + '=' + v;
        }
        var cls = Array.from(el.classList).filter(function(c) {
            return c.length >= 2 && !/^[a-z]+-[a-z0-9]{5,}$/.test(c);
        });
        if (cls.length) return '.' + cls[0];
        return '';
    }

    // ── 区域类型检测 ──
    function detectType(el) {
        var tag = el.tagName.toLowerCase();
        var id = (el.id || '').toLowerCase();
        var clsStr = el.className ? el.className.toString().toLowerCase() : '';
        var role = (el.getAttribute('role') || '').toLowerCase();
        // 将 class 按空白拆分为独立类名
        var clsNames = clsStr.split(/\s+/).filter(function(c) { return c; });
        // clsMatch: 类名等于 keyword 或以 keyword- 开头（如 header-v2, footer-link）
        // 排除 has-header, page-header 等
        function clsMatch(keyword) {
            return clsNames.some(function(c) {
                return c === keyword || c.indexOf(keyword + '-') === 0;
            });
        }

        // 语义标签 / ARIA
        if (tag === 'header' || role === 'banner')       return 'header';
        if (tag === 'nav' || role === 'navigation')      return 'nav';
        if (tag === 'main' || role === 'main')           return 'main';
        if (tag === 'footer' || role === 'contentinfo')  return 'footer';
        if (tag === 'aside' || role === 'complementary') return 'sidebar';
        if (tag === 'article' || role === 'article')     return 'content';
        if (tag === 'form' || role === 'search' || role === 'form') return 'search';

        // ID 匹配（支持前缀：footer-wrapper → footer）
        if (id === 'header' || id === 'hd' || id.indexOf('header') === 0) return 'header';
        if (id === 'footer' || id === 'ft' || id.indexOf('footer') === 0) return 'footer';
        if (id === 'sidebar' || id === 'aside') return 'sidebar';
        if (id === 'main' || id === 'content' || id.indexOf('content') !== -1) return 'main';

        // class 启发式：clsMatch 只匹配 keyword 或 keyword-* 开头
        if (clsMatch('header') || clsMatch('navbar') || clsMatch('topbar')) return 'header';
        if (clsMatch('footer') || clsMatch('copyright') || clsMatch('yclinks')) return 'footer';
        if (clsMatch('sidebar') || clsMatch('aside')) return 'sidebar';
        if (clsMatch('search') && !clsMatch('result')) return 'search';
        if (clsMatch('filter')) return 'filter';
        if (clsMatch('job-list') || clsMatch('card-list') || clsMatch('recommend-result') || clsMatch('list-container')) return 'list';
        if (clsMatch('content') || clsMatch('detail') || clsMatch('article') || clsMatch('richtext')) return 'content';

        // 位置启发式（保守策略）
        try {
            var rect = el.getBoundingClientRect();
            var links = el.querySelectorAll('a').length;
            var inputs = el.querySelectorAll('input[type=text],input[type=search],input:not([type]),textarea').length;
            // 有文本输入框 + 总交互不多 → 搜索区
            if (inputs > 0 && links + inputs <= 8) return 'search';
            // 页面顶部、纯链接、高度小 → 导航栏
            if (rect.top < 80 && links >= 4 && rect.height < 120 && inputs === 0) return 'header';
        } catch(e) {}

        return null;
    }

    // ── 区域中文标签 ──
    var TYPE_LABELS = {
        header: '导航栏', nav: '导航', main: '主体区域', footer: '页脚',
        sidebar: '侧边栏', content: '内容区', search: '搜索区', filter: '筛选区',
        list: '列表区', other: '其他'
    };

    // ── 交互元素选择器 ──
    var INTERACTIVE_SEL = 'a,button,input:not([type=hidden]),select,textarea,[role=button],[role=link],[role=tab],[role=menuitem],[role=checkbox],[role=radio],[tabindex]:not([tabindex="-1"])';

    // ── 获取某区域内的交互元素（排除子区域） ──
    function getElements(el, excludeEls) {
        var all = el.querySelectorAll(INTERACTIVE_SEL);
        var results = [];
        var seen = new Set();

        for (var i = 0; i < all.length && results.length < 80; i++) {
            var item = all[i];
            // 排除子区域内的元素
            var inExcluded = false;
            for (var j = 0; j < excludeEls.length; j++) {
                if (excludeEls[j] !== el && excludeEls[j].contains(item)) { inExcluded = true; break; }
            }
            if (inExcluded) continue;

            if (!isVisible(item)) continue;

            var tag = item.tagName.toLowerCase();
            var text = (item.innerText || item.value || item.getAttribute('aria-label') ||
                        item.getAttribute('title') || item.getAttribute('placeholder') || '').trim().substring(0, 80);

            var key = tag + '|' + text + '|' + (item.href || '');
            if (seen.has(key) || !text) continue;
            seen.add(key);

            var role = item.getAttribute('role') || '';
            if (!role) {
                if (tag === 'a') role = 'link';
                else if (tag === 'button' || item.type === 'submit') role = 'button';
                else if (tag === 'input') role = item.type || 'textbox';
                else if (tag === 'select') role = 'combobox';
                else if (tag === 'textarea') role = 'textbox';
            }

            var entry = { tag: tag, role: role, text: text, loc: getLoc(item) };
            if (item.type && !['submit','button','text'].includes(item.type)) entry.type = item.type;
            if (item.placeholder) entry.placeholder = item.placeholder;
            if (tag === 'a' && item.href) entry.href = item.href;
            results.push(entry);
        }
        return results;
    }

    // ── 重复模式检测 ──
    function detectPattern(el) {
        var classGroups = {};
        for (var i = 0; i < el.children.length; i++) {
            var child = el.children[i];
            if (!isVisible(child)) continue;
            var cls = child.className ? child.className.toString().trim().split(/\s+/)[0] : '';
            if (cls && cls.length >= 3) {
                if (!classGroups[cls]) classGroups[cls] = [];
                classGroups[cls].push(child);
            }
        }
        var bestCls = null, bestCount = 0;
        for (var c in classGroups) {
            if (classGroups[c].length > bestCount) {
                bestCount = classGroups[c].length;
                bestCls = c;
            }
        }
        if (bestCount < 3) {
            // 尝试二级子元素的重复
            var allDescClasses = {};
            el.querySelectorAll(':scope > * > [class]').forEach(function(d) {
                var dc = d.className.toString().trim().split(/\s+/)[0];
                if (dc && dc.length >= 3) {
                    if (!allDescClasses[dc]) allDescClasses[dc] = 0;
                    allDescClasses[dc]++;
                }
            });
            for (var dc in allDescClasses) {
                if (allDescClasses[dc] > bestCount) {
                    bestCount = allDescClasses[dc];
                    bestCls = dc;
                }
            }
            if (bestCount < 3) return null;
        }

        // 分析样本卡片结构
        var sampleEl = (classGroups[bestCls] || [null])[0] || el.querySelector('.' + bestCls);
        var sampleFields = [];
        if (sampleEl) {
            var leafEls = sampleEl.querySelectorAll('[class]');
            var fieldSeen = new Set();
            for (var k = 0; k < leafEls.length && sampleFields.length < 8; k++) {
                var le = leafEls[k];
                var leTag = le.tagName.toLowerCase();
                if (['script','style','svg'].includes(leTag)) continue;
                var leText = (le.innerText || '').trim();
                if (!leText || leText.length < 2 || leText.length > 300) continue;
                // 只取叶子
                var childTexts = Array.from(le.children).map(function(c) { return (c.innerText||'').trim(); });
                var isLeaf = !childTexts.some(function(t) { return t.length > 1; });
                if (!isLeaf) continue;
                var leCls = le.className.toString().trim().split(/\s+/)[0];
                if (leCls && !fieldSeen.has(leCls)) {
                    fieldSeen.add(leCls);
                    sampleFields.push({ cls: leCls, sample: leText.substring(0, 60) });
                }
            }
        }

        return {
            selector: 'css:.' + bestCls,
            count: bestCount,
            sampleFields: sampleFields
        };
    }

    // ── 区域标识符 ──
    function getSectionLabel(el, type) {
        var id = el.id || '';
        var cls = el.className ? el.className.toString().trim().split(/\s+/)[0] : '';
        var tag = el.tagName.toLowerCase();
        var parts = [];
        if (id) parts.push('#' + id);
        else if (cls) parts.push('.' + cls);
        else parts.push(tag);
        return parts.join(' ');
    }

    // ── 递归构建区域树 ──
    function buildSections(el, depth, maxDepth) {
        if (!el || el.nodeType !== 1 || depth > maxDepth) return null;

        var tag = el.tagName.toLowerCase();
        if (SKIP.has(tag)) return null;

        var type = detectType(el);
        var interactiveCount = el.querySelectorAll(INTERACTIVE_SEL).length;
        var textLen = (el.innerText || '').trim().length;

        // 跳过空区域
        if (interactiveCount === 0 && textLen < 20) return null;

        // 构建子区域
        var childSections = [];
        var childSectionEls = [];
        for (var i = 0; i < el.children.length; i++) {
            var child = buildSections(el.children[i], depth + 1, maxDepth);
            if (child) {
                childSections.push(child);
                childSectionEls.push(el.children[i]);
            }
        }

        var id = el.id || '';
        var cls = el.className ? el.className.toString().trim().split(/\s+/)[0] : '';

        // 判断是否有意义
        var hasMeaningfulId = id && id.length >= 2;
        var hasMeaningfulClass = cls && cls.length >= 3;
        var isSemanticTag = ['header','nav','main','footer','aside','section','article','form'].includes(tag);
        var isMeaningful = type || hasMeaningfulId || isSemanticTag;

        // 透传节点：table/tbody/tr/td/center 等布局容器
        var isLayoutTag = ['table','tbody','thead','tfoot','tr','td','th','center','dl','dd','dt'].includes(tag);
        // 无意义的中间节点：直接透传子节点
        if ((!isMeaningful || isLayoutTag) && depth > 0 && !hasMeaningfulId) {
            if (childSections.length === 1) return childSections[0];
            if (childSections.length === 0) {
                // 有交互元素：保留为匿名区域（fall through）
                if (interactiveCount > 0 && hasMeaningfulClass) {
                    // fall through
                } else {
                    return null;
                }
            }
            // 多个子区域：如果有标识则保留为分组，否则透传
            if (childSections.length > 1 && !hasMeaningfulClass) {
                // 无标识，无法合并多个子区域 — 保留为虚拟容器
            }
        }

        // 获取本区域的直接交互元素
        var elements = getElements(el, childSectionEls);

        // 检测重复模式
        var pattern = detectPattern(el);

        if (!type) type = 'other';
        var label = TYPE_LABELS[type] || type;
        var sectionId = getSectionLabel(el, type);

        var section = {
            tag: tag,
            type: type,
            label: label,
            sectionId: sectionId
        };
        if (id) section.id = id;
        if (cls) section.cls = cls;
        section.interactiveCount = interactiveCount;
        section.textLen = textLen;
        if (elements.length > 0) section.elements = elements;
        if (pattern) section.pattern = pattern;
        if (childSections.length > 0) section.children = childSections;

        return section;
    }

    var root = document.body || document.documentElement;
    var result = buildSections(root, 0, 10);
    return result;
})()
"""

# ── JS：主体内容提取 ──────────────────────────────────────────────────────────
# 保留原有逻辑：评分找最佳根容器 → 按语义标签遍历 → markdown 输出

_JS_CONTENT_BLOCKS = r"""
return (function() {
    var SKIP_TAGS = new Set(['script','style','noscript','head','meta','link',
                             'svg','path','defs','symbol','use','g',
                             'iframe','template','canvas','video','audio']);
    var NOISE_TAGS = new Set(['header','footer','nav','aside']);
    var NOISE_ROLES = new Set(['navigation','banner','contentinfo','complementary',
                               'toolbar','menubar','search']);
    var BLOCK_TAGS = new Set([
        'h1','h2','h3','h4','h5','h6',
        'p','li','dt','dd','td','th',
        'blockquote','pre','figcaption','caption',
        'summary','label','legend'
    ]);
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

    function htmlToMarkdown(el, inList) {
        if (!el) return '';
        if (el.nodeType === 3) return el.nodeValue || '';
        if (el.nodeType !== 1) return '';

        var tag = el.tagName.toLowerCase();
        var result = '';

        if (tag === 'a') {
            var href = el.getAttribute('href') || '';
            var text = '';
            for (var i = 0; i < el.childNodes.length; i++) {
                text += htmlToMarkdown(el.childNodes[i], inList);
            }
            if (href && text.trim()) return '[' + text.trim() + '](' + href + ')';
            return text;
        }
        if (tag === 'img') {
            var src = el.getAttribute('src') || '';
            var alt = el.getAttribute('alt') || '';
            if (src) return '![' + alt + '](' + src + ')';
            return '';
        }
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
        if (tag === 'ul') {
            for (var i = 0; i < el.childNodes.length; i++) {
                if (el.childNodes[i].tagName && el.childNodes[i].tagName.toLowerCase() === 'li') {
                    result += '- ' + htmlToMarkdown(el.childNodes[i], true).trim() + '\n';
                }
            }
            return result;
        }
        if (tag === 'li') {
            for (var i = 0; i < el.childNodes.length; i++) {
                var childText = htmlToMarkdown(el.childNodes[i], true);
                result += childText.replace(/^[ \t]+/gm, '').replace(/[ \t]+/g, ' ');
            }
            return result.trim();
        }
        for (var i = 0; i < el.childNodes.length; i++) {
            result += htmlToMarkdown(el.childNodes[i], inList);
        }
        return result;
    }

    function findBestRoot() {
        var byTag = document.querySelector('article, [role="article"], main, [role="main"]');
        if (byTag) return byTag;
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
                if (area < 5000 || area > vpArea * 0.95) continue;
                var tlen = (el.textContent || '').replace(/\s+/g,' ').trim().length;
                if (tlen > 100) return el;
            }
        }
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

    function traverse(el, depth, root, results) {
        if (!el || el.nodeType !== 1) return;
        var tag = el.tagName.toLowerCase();
        if (SKIP_TAGS.has(tag)) return;
        if (isNoise(el)) return;
        if (!CODE_TAGS.has(tag) && !isVisible(el)) return;

        if (BLOCK_TAGS.has(tag)) {
            var clone = el.cloneNode(true);
            clone.querySelectorAll('style,script,noscript').forEach(function(n){n.remove();});
            clone.querySelectorAll('footer,aside').forEach(function(n){n.remove();});
            clone.querySelectorAll('[class*="footer"],[class*="copyright"],[class*="icp"],[class*="beian"],[class*="boss-info"],[class*="hot-link"],[class*="hot"],[class*="recommend"],[class*="sidebar"],[class*="breadcrumb"],[class*="c-breadcrumb"],[class*="toolbar"]').forEach(function(n){n.remove();});
            var text = htmlToMarkdown(clone).trim();
            if (!CODE_TAGS.has(tag)) {
                text = text.replace(/^[ \t]+- /gm, '- ').replace(/^[ \t]+\d+\. /gm, function(m) {
                    return m.replace(/^[ \t]+/, '');
                }).replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
            }
            if (text.length > 0) {
                results.push({ depth: depth, tag: tag, text: text, loc: getCssPath(el) });
            }
            return;
        }
        for (var i = 0; i < el.children.length; i++) {
            traverse(el.children[i], depth + 1, root, results);
        }
    }

    var root = findBestRoot();
    var rootTag = root.tagName.toLowerCase();
    var rootCls = root.className ? root.className.toString().trim().split(/\s+/)[0] : '';
    var rootId  = root.id || '';

    var results = [];
    for (var i = 0; i < root.children.length; i++) {
        traverse(root.children[i], 1, root, results);
    }

    return {
        root_tag: rootTag, root_cls: rootCls, root_id: rootId,
        root_loc: getCssPath(root), nodes: results
    };
})()
"""

# ── JS：扁平交互元素列表（兼容旧模式） ─────────────────────────────────────────

_JS_INTERACTIVE = r"""
return (function() {
    var INTERACTIVE_ROLES = new Set([
        'button','link','textbox','searchbox','combobox','listbox',
        'checkbox','radio','switch','slider','spinbutton','menuitem',
        'menuitemcheckbox','menuitemradio','option','tab','treeitem',
        'gridcell','columnheader','rowheader'
    ]);
    var INTERACTIVE_TAGS = new Set(['input','button','select','textarea','a']);
    var NOISE_CONTAINERS = new Set(['footer','aside']);

    function getPageTitle() {
        var selectors = ['h1','article h1','article h2','[class*="title"]',
                         '.post-title','.article-title','[class*="Title"]'];
        for (var sel of selectors) {
            var el = document.querySelector(sel);
            if (el && el.innerText && el.innerText.trim()) {
                var text = el.innerText.trim();
                if (text.length >= 5 && text.length <= 100) return text;
            }
        }
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
            if (el.id && /^[a-zA-Z][\w-]*$/.test(el.id)) { parts.unshift('#' + el.id); break; }
            var cls = Array.from(el.classList).filter(function(c){ return c.length >= 2; });
            var seg;
            if (cls.length > 0) {
                seg = '.' + cls[0];
                var sibs = el.parentElement ? Array.from(el.parentElement.querySelectorAll(':scope > ' + seg)) : [];
                if (sibs.length > 1) seg += ':nth-child(' + (Array.from(el.parentElement.children).indexOf(el)+1) + ')';
            } else {
                seg = el.tagName.toLowerCase();
                var tagSibs = el.parentElement ? Array.from(el.parentElement.children).filter(function(c){ return c.tagName === el.tagName; }) : [];
                if (tagSibs.length > 1) seg += ':nth-child(' + (Array.from(el.parentElement.children).indexOf(el)+1) + ')';
            }
            parts.unshift(seg);
            el = el.parentElement;
        }
        return parts.join(' > ');
    }

    function getLoc(el) {
        if (el.id && /^[a-zA-Z][\w-]*$/.test(el.id)) return '#' + el.id;
        var t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
        if (t && t.length <= 30 && t.length >= 1) return 'text:' + t;
        for (var attr of ['data-testid','data-qa','data-cy','aria-label','name','placeholder']) {
            var v = el.getAttribute(attr);
            if (v && v.length <= 60) return '@' + attr + '=' + v;
        }
        var cls = Array.from(el.classList).filter(function(c){
            return c.length >= 2 && !/^[a-z]+-[a-z0-9]{5,}$/.test(c);
        });
        if (cls.length) return '.' + cls[0];
        return 'css:' + getCssPath(el);
    }

    var results = [];
    var idx = 0;
    var seen = new Set();

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
        var key = el.tagName + '|' + (el.id||'') + '|' + (el.className||'') + '|' + (el.getAttribute('name')||'') + '|' + text;
        if (seen.has(key)) return;
        seen.add(key);

        var inNoise = inNoiseContainer(el);
        var item = { idx: idx++, tag: tag, role: role, text: text, loc: getLoc(el), in_nav: inNoise };
        if (el.type) item.type = el.type;
        if (el.placeholder) item.placeholder = el.placeholder;
        if (tag === 'a' && el.href) item.href = el.href;
        if (el.getAttribute('aria-label')) item.aria_label = el.getAttribute('aria-label');
        results.push(item);
    });

    // header 专项补充
    var headerLinks = document.querySelectorAll('#header a, header a');
    headerLinks.forEach(function(el) {
        if (!isVisible(el)) return;
        var tag = el.tagName.toLowerCase();
        var text = (el.innerText || el.value || el.getAttribute('aria-label') ||
                    el.getAttribute('title') || el.getAttribute('placeholder') || '').trim().substring(0, 100);
        var key = el.tagName + '|' + (el.id||'') + '|' + (el.className||'') + '|' + (el.getAttribute('name')||'') + '|' + text;
        if (seen.has(key)) return;
        seen.add(key);
        var item = { idx: idx++, tag: tag, role: 'link', text: text, loc: getLoc(el), in_nav: false };
        if (el.href) item.href = el.href;
        if (el.getAttribute('aria-label')) item.aria_label = el.getAttribute('aria-label');
        results.push(item);
    });

    return { interactive: results, page_title: getPageTitle() };
})()
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
