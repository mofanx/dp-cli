"""
snapshot 小模块单元测试：utils.py、clickable.py、extract.py
"""
import pytest

from dp_cli.snapshot import utils, clickable, extract


# ─────────────────────────────────────────────────────────────────────────────
# 假对象定义（复用）
# ─────────────────────────────────────────────────────────────────────────────

class FakeEle:
    """假 DrissionPage 元素。"""
    def __init__(self, tag="div", attrs=None, raw_text="", inner_html="",
                 html="", children=None, js_return=""):
        self.tag = tag
        self.attrs = attrs or {}
        self.raw_text = raw_text
        self.inner_html = inner_html
        self.html = html
        self._children = children or {}   # sel -> FakeEle / list
        self._js_return = js_return
    
    def attr(self, name):
        return self.attrs.get(name)
    
    def ele(self, sel):
        v = self._children.get(sel)
        return v if v is not None else NoneEle()
    
    def eles(self, sel):
        v = self._children.get(sel)
        return v if isinstance(v, list) else []
    
    def run_js(self, script):
        return self._js_return


class NoneEle:
    """模拟 DrissionPage 的 NoneElement(未找到)。"""
    pass

NoneEle.__name__ = "NoneElement"  # 让 __class__.__name__ == 'NoneElement'


class FakePage:
    def __init__(self, eles=None, run_js_return=None, cdp_return=None,
                 run_js_raises=False):
        self._eles = eles or []
        self._run_js_return = run_js_return
        self._cdp_return = cdp_return or {}
        self._run_js_raises = run_js_raises
        
        class _Wait:
            def doc_loaded(self): pass
        
        self.wait = _Wait()
        self.run_js_calls = []
    
    def eles(self, sel, timeout=None):
        return self._eles
    
    def s_eles(self, sel):
        return self._eles
    
    def run_js(self, script, *a, **k):
        self.run_js_calls.append(script)
        if self._run_js_raises:
            raise RuntimeError("js failed")
        return self._run_js_return
    
    def run_cdp(self, method, *a, **k):
        return self._cdp_return


# ─────────────────────────────────────────────────────────────────────────────
# 3.1 utils.suggest_locator
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_suggest_locator_id():
    """attrs 有 id → 返回 #id。"""
    assert utils.suggest_locator('div', {'id': 'btn'}, '') == '#btn'


@pytest.mark.unit
def test_suggest_locator_semantic():
    """无 id 有 data-testid → 返回 @data-testid=...;再测 aria-label 命中。"""
    assert utils.suggest_locator('div', {'data-testid': 'submit'}, '') == '@data-testid=submit'
    assert utils.suggest_locator('div', {'aria-label': 'Close'}, '') == '@aria-label=Close'


@pytest.mark.unit
def test_suggest_locator_class():
    """只有有意义 class → 返回 .class。"""
    assert utils.suggest_locator('div', {'class': 'btn-primary'}, '') == '.btn-primary'


@pytest.mark.unit
def test_suggest_locator_text():
    """无 id/语义/class,text ≤30 → 返回 text:...。"""
    assert utils.suggest_locator('span', {}, 'Submit') == 'text:Submit'


@pytest.mark.unit
def test_suggest_locator_fallback_tag():
    """啥都没有 → 返回 t:<tag>。"""
    assert utils.suggest_locator('div', {}, '') == 't:div'


# ─────────────────────────────────────────────────────────────────────────────
# 3.2 utils._is_meaningful_class
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_meaningful_class_normal_true():
    """如 btn-primary、header → True。"""
    assert utils._is_meaningful_class('btn-primary') == True
    assert utils._is_meaningful_class('header') == True


@pytest.mark.unit
def test_meaningful_class_hash_false():
    """混淆类名 → False。测至少 2 种:大小写混杂无分隔、全小写无元音。"""
    assert utils._is_meaningful_class('hkJMPzDNh') == False
    assert utils._is_meaningful_class('bcdfgh') == False


@pytest.mark.unit
def test_meaningful_class_too_short_false():
    """'' 或单字符 → False。"""
    assert utils._is_meaningful_class('') == False
    assert utils._is_meaningful_class('a') == False


# ─────────────────────────────────────────────────────────────────────────────
# 3.3 clickable.format_clickable_record
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_format_record_basic():
    """high 置信、有 label/zone/locator → 输出形如 [5] button "Sign in" @top-right → #signin。"""
    rec = {
        'confidence': 'high',
        'tag': 'button',
        'label': 'Sign in',
        'zone': 'top-right',
        'locator': '#signin'
    }
    result = clickable.format_clickable_record(rec, 5)
    assert '[5]' in result
    assert 'button' in result
    assert '"Sign in"' in result
    assert '@top-right' in result
    assert '→ #signin' in result


@pytest.mark.unit
def test_format_record_medium_marker():
    """confidence=medium → 含 ⚡。"""
    rec = {
        'confidence': 'medium',
        'tag': 'div',
        'label': 'Menu',
        'locator': '.menu'
    }
    result = clickable.format_clickable_record(rec, 1)
    assert '⚡' in result


@pytest.mark.unit
def test_format_record_low_marker():
    """confidence=low → 含 ?。"""
    rec = {
        'confidence': 'low',
        'tag': 'span',
        'label': 'More',
        'locator': 'css:.more'
    }
    result = clickable.format_clickable_record(rec, 2)
    assert '?' in result


@pytest.mark.unit
def test_format_record_icon_only():
    """无 label 但 iconOnly=True → 含 (icon)。"""
    rec = {
        'confidence': 'high',
        'tag': 'svg',
        'iconOnly': True,
        'locator': 'xpath://svg'
    }
    result = clickable.format_clickable_record(rec, 3)
    assert '(icon)' in result


@pytest.mark.unit
def test_format_record_verbose():
    """verbose=True + rect 有 w/h → 含尺寸如 80x32。"""
    rec = {
        'confidence': 'high',
        'tag': 'button',
        'label': 'OK',
        'locator': '#ok',
        'reason': 'button',
        'rect': {'w': 80, 'h': 32}
    }
    result = clickable.format_clickable_record(rec, 4, verbose=True)
    assert '80x32' in result
    assert 'button' in result


@pytest.mark.unit
def test_format_record_label_truncated():
    """label 长度 >80 → 被截断且含 …。"""
    rec = {
        'confidence': 'high',
        'tag': 'div',
        'label': 'a' * 100,
        'locator': '.long'
    }
    result = clickable.format_clickable_record(rec, 5)
    assert '…' in result


# ─────────────────────────────────────────────────────────────────────────────
# 3.4 clickable._walk
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_walk_builds_bid_map():
    """构造一个含 backendNodeId + attributes(扁平 list)+ children 的节点,
    调 clickable._walk(node, {}),断言 bid_map 里有该 bid、tag 小写、attrs 正确成对解析。"""
    node = {
        'backendNodeId': 123,
        'nodeName': 'DIV',
        'attributes': ['id', 'test', 'class', 'btn'],
        'children': []
    }
    bid_map = {}
    clickable._walk(node, bid_map)
    
    assert 123 in bid_map
    assert bid_map[123]['tag'] == 'div'
    assert bid_map[123]['attrs'] == {'id': 'test', 'class': 'btn'}


@pytest.mark.unit
def test_walk_pierces_shadow_and_iframe():
    """节点带 shadowRoots 和 contentDocument 子树 → 内部 bid 也被收录。"""
    node = {
        'backendNodeId': 1,
        'nodeName': 'DIV',
        'attributes': [],
        'shadowRoots': [
            {
                'backendNodeId': 2,
                'nodeName': 'SPAN',
                'attributes': ['id', 'shadow'],
                'children': []
            }
        ],
        'contentDocument': {
            'backendNodeId': 3,
            'nodeName': 'IFRAME',
            'attributes': ['src', 'test'],
            'children': []
        }
    }
    bid_map = {}
    clickable._walk(node, bid_map)
    
    assert 1 in bid_map
    assert 2 in bid_map
    assert 3 in bid_map


# ─────────────────────────────────────────────────────────────────────────────
# 3.5 clickable.detect_clickables
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_detect_clickables_js_error():
    """FakePage(run_js_raises=True) → 返回 dict 的 method == 'failed',elements == []。"""
    page = FakePage(run_js_raises=True)
    result = clickable.detect_clickables(page)
    
    assert result['method'] == 'failed'
    assert result['elements'] == []


@pytest.mark.unit
def test_detect_clickables_bad_return():
    """run_js 返回非 dict(如 [])→ method == 'failed'。"""
    page = FakePage(run_js_return=[])
    result = clickable.detect_clickables(page)
    
    assert result['method'] == 'failed'
    assert result['elements'] == []


@pytest.mark.unit
def test_detect_clickables_happy():
    """run_js 返回探测结果,run_cdp 返回 DOM 树;断言返回 method == 'js+cdp'、total == 1、元素有 locator 字段、backendNodeId 被关联。"""
    js_result = {
        'elements': [{'scanId': 1, 'tag': 'button', 'text': 'OK', 'rect': {'x': 0, 'y': 0, 'w': 50, 'h': 20}}],
        'truncated': False
    }
    
    cdp_result = {
        'root': {
            'backendNodeId': 999,
            'nodeName': 'HTML',
            'attributes': [],
            'children': [
                {
                    'backendNodeId': 1,
                    'nodeName': 'BUTTON',
                    'attributes': ['data-dp-scan-id', '1', 'id', 'btn'],
                    'children': []
                }
            ]
        }
    }
    
    page = FakePage(run_js_return=js_result, cdp_return=cdp_result)
    result = clickable.detect_clickables(page)
    
    assert result['method'] == 'js+cdp'
    assert result['total'] == 1
    assert len(result['elements']) == 1
    assert result['elements'][0]['locator'] == '#btn'
    assert result['elements'][0]['backendNodeId'] == 1


# ─────────────────────────────────────────────────────────────────────────────
# 3.6 extract.extract_structured
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_extract_structured_empty():
    """FakePage(eles=[]) → 返回 []。"""
    page = FakePage(eles=[])
    result = extract.extract_structured(page, '.item', {'title': '.title'})
    assert result == []


@pytest.mark.unit
def test_extract_structured_text_field():
    """一个容器 item,item.ele('.title') 返回 FakeEle(raw_text='标题'),
    fields={'title': '.title'} → 返回 [{'title': '标题'}]。"""
    item = FakeEle(children={'.title': FakeEle(raw_text='标题')})
    page = FakePage(eles=[item])
    result = extract.extract_structured(page, '.item', {'title': '.title'})
    
    assert result == [{'title': '标题'}]


@pytest.mark.unit
def test_extract_structured_attr_field():
    """spec 用 dict {'selector': 'a', 'attr': 'href'},元素 attrs 含 href → 取到属性值。"""
    item = FakeEle(children={
        'a': FakeEle(attrs={'href': 'https://example.com'})
    })
    page = FakePage(eles=[item])
    result = extract.extract_structured(page, '.item', {'link': {'selector': 'a', 'attr': 'href'}})
    
    assert result == [{'link': 'https://example.com'}]


@pytest.mark.unit
def test_extract_structured_missing_uses_default():
    """item.ele() 返回 NoneElement → 用 spec 的 default(自定义一个 default 值,断言生效)。"""
    item = FakeEle(children={})
    page = FakePage(eles=[item])
    result = extract.extract_structured(page, '.item', {'title': {'selector': '.title', 'default': 'N/A'}})
    
    assert result == [{'title': 'N/A'}]


@pytest.mark.unit
def test_extract_structured_multi():
    """spec {'selector': 'li', 'multi': True},item.eles('li') 返回多个 FakeEle → 返回 list 形式的文本。"""
    item = FakeEle(children={
        'li': [FakeEle(raw_text='item1'), FakeEle(raw_text='item2')]
    })
    page = FakePage(eles=[item])
    result = extract.extract_structured(page, '.item', {'items': {'selector': 'li', 'multi': True}})
    
    assert result == [{'items': ['item1', 'item2']}]


@pytest.mark.unit
def test_extract_structured_limit():
    """eles 返回 5 个,limit=2 → 结果只有 2 条。"""
    page = FakePage(eles=[FakeEle() for _ in range(5)])
    result = extract.extract_structured(page, '.item', {}, limit=2)
    
    assert len(result) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 3.7 extract.query_elements
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_query_text_and_tag():
    """fields=['text','tag'],FakeEle(tag='a', raw_text='链接') → 记录含 text=='链接'、tag=='a'。"""
    ele = FakeEle(tag='a', raw_text='链接')
    page = FakePage(eles=[ele])
    result = extract.query_elements(page, 'a', ['text', 'tag'])
    
    assert result[0]['text'] == '链接'
    assert result[0]['tag'] == 'a'


@pytest.mark.unit
def test_query_loc_field():
    """fields=['loc'] → 调用 suggest_locator,记录含 loc(给元素 attrs 个 id 便于断言 #id)。"""
    ele = FakeEle(tag='div', attrs={'id': 'test'}, raw_text='')
    page = FakePage(eles=[ele])
    result = extract.query_elements(page, 'div', ['loc'])
    
    assert result[0]['loc'] == '#test'


@pytest.mark.unit
def test_query_css_field():
    """fields=['css'],FakeEle.run_js 返回 body > div → 记录 css == 'css:body > div'。"""
    ele = FakeEle(js_return='body > div')
    page = FakePage(eles=[ele])
    result = extract.query_elements(page, 'div', ['css'])
    
    assert result[0]['css'] == 'css:body > div'


@pytest.mark.unit
def test_query_attr_passthrough():
    """fields=['href'],元素 attrs 含 href → 记录 href 为该值。"""
    ele = FakeEle(attrs={'href': 'https://example.com'})
    page = FakePage(eles=[ele])
    result = extract.query_elements(page, 'a', ['href'])
    
    assert result[0]['href'] == 'https://example.com'


@pytest.mark.unit
def test_query_limit():
    """eles 多于 limit → 截断。"""
    page = FakePage(eles=[FakeEle() for _ in range(10)])
    result = extract.query_elements(page, 'div', ['text'], limit=3)
    
    assert len(result) == 3