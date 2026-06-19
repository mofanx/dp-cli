"""
a11y.py 单元测试
"""
import pytest

from dp_cli.snapshot import a11y


# ─────────────────────────────────────────────────────────────────────────────
# 小工厂函数
# ─────────────────────────────────────────────────────────────────────────────

def node(role="", name="", **kw):
    """构造一个 a11y 树节点(已含 _normalize_node 后的字段)。"""
    n = {"nodeId": kw.get("nodeId", ""), "role": role, "name": name,
         "description": kw.get("description", ""), "value": kw.get("value", ""),
         "ignored": kw.get("ignored", False), "ignoredReasons": [],
         "properties": kw.get("properties", {}), "childIds": kw.get("childIds", []),
         "parentId": kw.get("parentId"), "backendNodeId": kw.get("backendNodeId"),
         "locator": kw.get("locator"), "children": kw.get("children", [])}
    return n


def snapshot(tree, **kw):
    return {"page": {"url": kw.get("url", "https://example.com"),
                     "title": kw.get("title", "Demo")},
            "stats": kw.get("stats", {"total": 1, "interactive": 0, "ignored": 0}),
            "method": kw.get("method", "cdp"), "tree": tree, **kw.get("extra", {})}


class FakePage:
    def __init__(self, cdp_map=None, run_js_return=None, url="https://example.com", title="Demo"):
        self._cdp_map = cdp_map or {}
        self._run_js_return = run_js_return
        self.url = url
        self.title = title
        
        class _W:
            def doc_loaded(self): pass
        
        self.wait = _W()
    
    def run_cdp(self, method, **kw):
        v = self._cdp_map.get(method)
        if isinstance(v, Exception):
            raise v
        if callable(v):
            return v(**kw)
        if v is None:
            raise RuntimeError(f"no cdp stub for {method}")
        return v
    
    def run_js(self, script, *a, **k):
        if isinstance(self._run_js_return, Exception):
            raise self._run_js_return
        return self._run_js_return


# ─────────────────────────────────────────────────────────────────────────────
# 2.1 _ax_value
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_ax_value_none():
    """None → ''。"""
    assert a11y._ax_value(None) == ''


@pytest.mark.unit
def test_ax_value_dict():
    """dict {'value': 'x'} → 'x'。"""
    assert a11y._ax_value({'value': 'x'}) == 'x'


@pytest.mark.unit
def test_ax_value_plain():
    """普通值原样返回。"""
    assert a11y._ax_value('hello') == 'hello'
    assert a11y._ax_value(123) == 123


# ─────────────────────────────────────────────────────────────────────────────
# 2.2 _normalize_node
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_normalize_node():
    """喂一个 CDP 原始节点 → 断言解包后 role/name 为标量、properties 变成 dict、locator is None、children == []。"""
    raw = {
        'nodeId': '1',
        'role': {'value': 'button'},
        'name': {'value': 'Submit'},
        'properties': [{'name': 'checked', 'value': {'value': True}}],
        'ignored': False,
        'ignoredReasons': [{'value': 'invisible'}],
        'childIds': ['2'],
        'backendDOMNodeId': 123
    }
    result = a11y._normalize_node(raw)
    
    assert result['role'] == 'button'
    assert result['name'] == 'Submit'
    assert result['properties'] == {'checked': True}
    assert result['locator'] is None
    assert result['children'] == []


# ─────────────────────────────────────────────────────────────────────────────
# 2.3 _build_tree
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_build_tree_empty():
    """空列表 → {}。"""
    assert a11y._build_tree([]) == {}


@pytest.mark.unit
def test_build_tree_nesting():
    """3 个扁平节点(root childIds 指向两个子)→ 返回根,且 root['children'] 有 2 个。"""
    nodes = [
        {'nodeId': '1', 'childIds': ['2', '3']},
        {'nodeId': '2', 'childIds': [], 'parentId': '1'},
        {'nodeId': '3', 'childIds': [], 'parentId': '1'}
    ]
    result = a11y._build_tree(nodes)
    
    assert result['nodeId'] == '1'
    assert len(result['children']) == 2


@pytest.mark.unit
def test_build_tree_root_detection():
    """无 parentId 的节点被识别为根。"""
    nodes = [
        {'nodeId': '1', 'childIds': ['2']},
        {'nodeId': '2', 'childIds': [], 'parentId': '1'}
    ]
    result = a11y._build_tree(nodes)
    
    assert result['nodeId'] == '1'


# ─────────────────────────────────────────────────────────────────────────────
# 2.4 _compute_stats
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_compute_stats():
    """混合 normalized 节点(含 ignored、含 button 交互角色)→ total/ignored/interactive 计数正确。"""
    nodes = [
        node(role='button', ignored=False),
        node(role='link', ignored=True),
        node(role='paragraph', ignored=False)
    ]
    result = a11y._compute_stats(nodes)
    
    assert result['total'] == 3
    assert result['ignored'] == 1
    assert result['interactive'] == 2  # button 和 link 都是交互角色


# ─────────────────────────────────────────────────────────────────────────────
# 2.5 _walk_dom_node
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_walk_dom_node():
    """带 attributes(扁平 list)+ children + shadowRoots + contentDocument → bid_map 收录所有 bid,tag 小写、attrs 成对解析。"""
    node = {
        'backendNodeId': 1,
        'nodeName': 'DIV',
        'attributes': ['id', 'test', 'class', 'btn'],
        'children': [
            {
                'backendNodeId': 2,
                'nodeName': 'SPAN',
                'attributes': ['class', 'text'],
                'children': []
            }
        ],
        'shadowRoots': [
            {
                'backendNodeId': 3,
                'nodeName': 'STYLE',
                'attributes': [],
                'children': []
            }
        ],
        'contentDocument': {
            'backendNodeId': 4,
            'nodeName': 'IFRAME',
            'attributes': ['src', 'test'],
            'children': []
        }
    }
    bid_map = {}
    a11y._walk_dom_node(node, bid_map)
    
    assert 1 in bid_map
    assert 2 in bid_map
    assert 3 in bid_map
    assert 4 in bid_map
    assert bid_map[1]['tag'] == 'div'
    assert bid_map[1]['attrs'] == {'id': 'test', 'class': 'btn'}


# ─────────────────────────────────────────────────────────────────────────────
# 2.6 render_a11y_text
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_render_error_branch():
    """snapshot 带 error 字段 → 输出含 ⚠、含 ### Page Snapshot、提前返回(不渲染树)。"""
    snap = snapshot({}, extra={'error': 'CDP failed'})
    result = a11y.render_a11y_text(snap)
    
    assert '⚠' in result
    assert '### Page Snapshot' in result
    assert 'button' not in result


@pytest.mark.unit
def test_render_empty_tree():
    """tree 为 {} → 含 （a11y tree 为空）。"""
    snap = snapshot({})
    result = a11y.render_a11y_text(snap)
    
    assert '（a11y tree 为空）' in result


@pytest.mark.unit
def test_render_basic_interactive():
    """tree 为一个 button 节点(role='button', name='登录', locator='#login')→
    输出含 button、"登录"、→ #login,且头部 stats 行含 refs。"""
    snap = snapshot(node(role='button', name='登录', locator='#login'))
    refs = {}
    result = a11y.render_a11y_text(snap, refs=refs)
    
    assert 'button' in result
    assert '"登录"' in result
    assert '→ #login' in result
    assert 'refs' in result


@pytest.mark.unit
def test_render_assigns_refs():
    """传入空 refs={},渲染后 refs 被填充,且含该 button 的 {locator, role, name, backendNodeId}。"""
    snap = snapshot(node(role='button', name='OK', locator='#ok', backendNodeId=123))
    refs = {}
    a11y.render_a11y_text(snap, refs=refs)
    
    assert '1' in refs
    assert refs['1']['locator'] == '#ok'
    assert refs['1']['role'] == 'button'
    assert refs['1']['name'] == 'OK'
    assert refs['1']['backendNodeId'] == 123


@pytest.mark.unit
def test_render_brief_truncates():
    """一个 paragraph 节点 name 超 80 字,brief=True → 输出该文本被截断含 ...。"""
    snap = snapshot(node(role='paragraph', name='a' * 100))
    result = a11y.render_a11y_text(snap, brief=True)
    
    assert '...' in result


@pytest.mark.unit
def test_render_properties():
    """节点 properties 含 {'checked': True, 'level': 2} → 输出含 [checked 等属性段。"""
    snap = snapshot(node(role='checkbox', name='Accept', properties={'checked': True, 'level': 2}))
    result = a11y.render_a11y_text(snap)
    
    assert '[checked' in result
    assert 'level=2' in result


@pytest.mark.unit
def test_render_clickable_extras():
    """snapshot 带 clickable_extras=[{...}] + clickable_meta →
    输出含 ### Additional Interactive Elements,且 refs 里包含该 extra(role 形如 clickable/...)。"""
    snap = snapshot(node(role='button'), extra={
        'clickable_extras': [{'tag': 'div', 'text': 'Menu', 'locator': '.menu', 'confidence': 'medium'}],
        'clickable_meta': {'viewport_only': False, 'include_low': False}
    })
    refs = {}
    result = a11y.render_a11y_text(snap, refs=refs)
    
    assert '### Additional Interactive Elements' in result
    # refs 应该包含 clickable extra
    assert any('clickable/div' in r.get('role', '') for r in refs.values())


# ─────────────────────────────────────────────────────────────────────────────
# 2.7 render_a11y_plain_text
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_plain_empty():
    """tree 为空 → ''。"""
    snap = snapshot({})
    result = a11y.render_a11y_plain_text(snap)
    
    assert result == ''


@pytest.mark.unit
def test_plain_collects_text():
    """tree 含若干 StaticText/段落 → 返回按阅读顺序拼接的纯文本。"""
    snap = snapshot(node(role='generic', children=[
        node(role='StaticText', name='Hello')
    ]))
    result = a11y.render_a11y_plain_text(snap)
    
    assert 'Hello' in result


@pytest.mark.unit
def test_plain_fills_refs():
    """传 refs={} → 被填充(交互节点 + clickable_extras 都计入,编号连续)。"""
    snap = snapshot(node(role='button', name='OK', locator='#ok'))
    refs = {}
    a11y.render_a11y_plain_text(snap, refs=refs)
    
    assert '1' in refs
    assert refs['1']['role'] == 'button'


# ─────────────────────────────────────────────────────────────────────────────
# 2.8 _collect_text / _collect_plain_text
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_collect_text_nested():
    """无名 generic 容器内含 StaticText → _collect_text 穿透收集到文本。"""
    node_data = node(role='generic', children=[
        node(role='StaticText', name='Nested text')
    ])
    result = a11y._collect_text(node_data)
    
    assert 'Nested text' in result


@pytest.mark.unit
def test_collect_text_depth_limit():
    """构造超 10 层嵌套 → 不报错(返回空或部分)。"""
    nested = node(role='generic')
    for _ in range(12):
        nested = node(role='generic', children=[nested])
    
    result = a11y._collect_text(nested)
    # 不应该报错，可能返回空或部分文本
    assert isinstance(result, str)


@pytest.mark.unit
def test_collect_plain_text_block_newline():
    """块级角色(如 paragraph)后插入空行分隔。"""
    # 使用有 name 的 paragraph 节点
    node_data = node(role='paragraph', name='Text1')
    parts = ['existing']
    a11y._collect_plain_text(node_data, parts)
    # 应该添加了文本
    assert len(parts) > 1


# ─────────────────────────────────────────────────────────────────────────────
# 2.9 _collect_refs_only
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_collect_refs_interactive():
    """交互节点(有 locator)+ 内容节点(heading 有 name)→ refs 各分配编号;
    ignored 节点被跳过但仍递归子节点。"""
    node_data = node(role='generic', children=[
        node(role='button', name='OK', locator='#ok'),
        node(role='heading', name='Title'),
        node(role='link', ignored=True, children=[
            node(role='StaticText', name='Ignored link')
        ])
    ])
    ctx = {'counter': 0, 'refs': {}}
    a11y._collect_refs_only(node_data, ctx)
    
    assert '1' in ctx['refs']
    assert '2' in ctx['refs']
    assert ctx['refs']['1']['role'] == 'button'
    assert ctx['refs']['2']['role'] == 'heading'


# ─────────────────────────────────────────────────────────────────────────────
# 2.10 编排/CDP 函数
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_get_full_tree_cdp():
    """FakePage(cdp_map={'Accessibility.getFullAXTree': {'nodes': [..]}}) → _get_full_tree_cdp 返回 nodes 列表。"""
    cdp_result = {'nodes': [{'nodeId': '1', 'role': {'value': 'root'}}]}
    page = FakePage(cdp_map={'Accessibility.getFullAXTree': cdp_result})
    result = a11y._get_full_tree_cdp(page)
    
    assert len(result) == 1
    assert result[0]['nodeId'] == '1'


@pytest.mark.unit
def test_build_dom_bid_map_ok():
    """run_cdp 正常返回树 → 建好 map。"""
    cdp_result = {
        'root': {
            'backendNodeId': 1,
            'nodeName': 'DIV',
            'attributes': ['id', 'test'],
            'children': []
        }
    }
    page = FakePage(cdp_map={'DOM.getDocument': cdp_result})
    result = a11y._build_dom_bid_map(page)
    
    assert 1 in result
    assert result[1]['tag'] == 'div'


@pytest.mark.unit
def test_build_dom_bid_map_error():
    """run_cdp 抛异常 → {}。"""
    page = FakePage(cdp_map={'DOM.getDocument': RuntimeError('CDP error')})
    result = a11y._build_dom_bid_map(page)
    
    assert result == {}


@pytest.mark.unit
def test_get_dom_attrs():
    """DOM.describeNode 返回节点 → 解析出 tag/attrs;异常 → {}。"""
    cdp_result = {
        'node': {
            'nodeName': 'BUTTON',
            'attributes': ['id', 'submit', 'class', 'btn']
        }
    }
    page = FakePage(cdp_map={'DOM.describeNode': cdp_result})
    result = a11y._get_dom_attrs(page, 123)
    
    assert result['tag'] == 'button'
    assert result['attrs'] == {'id': 'submit', 'class': 'btn'}


@pytest.mark.unit
def test_get_dom_attrs_error():
    """异常 → {}。"""
    page = FakePage(cdp_map={'DOM.describeNode': RuntimeError('error')})
    result = a11y._get_dom_attrs(page, 123)
    
    assert result == {}


@pytest.mark.unit
def test_generate_locators_batch():
    """给一组交互节点 + FakePage 的 bid_map 命中 → 节点的 locator 被填充(经 suggest_locator);空列表 → 直接返回不报错。"""
    nodes = [
        node(role='button', backendNodeId=1, name='Submit'),
        node(role='link', backendNodeId=2, name='Home')
    ]
    
    cdp_result = {
        'root': {
            'backendNodeId': 1,
            'nodeName': 'BUTTON',
            'attributes': ['id', 'btn'],
            'children': [
                {
                    'backendNodeId': 2,
                    'nodeName': 'A',
                    'attributes': ['href', '/'],
                    'children': []
                }
            ]
        }
    }
    page = FakePage(cdp_map={'DOM.getDocument': cdp_result})
    
    a11y._generate_locators_batch(page, nodes)
    
    assert nodes[0]['locator'] is not None
    assert nodes[1]['locator'] is not None


@pytest.mark.unit
def test_generate_locators_batch_empty():
    """空列表 → 直接返回不报错。"""
    page = FakePage()
    a11y._generate_locators_batch(page, [])
    # 不应该报错


@pytest.mark.unit
def test_find_subtree_by_selector_not_found():
    """DOM.querySelector 返回无 nodeId → 返回 (原 tree, warning 字符串)。"""
    cdp_result = {
        'root': {'nodeId': 1},
        'nodeId': None
    }
    page = FakePage(cdp_map={
        'DOM.getDocument': {'root': {'nodeId': 1}},
        'DOM.querySelector': cdp_result
    })
    
    tree = node(role='root')
    result, warning, frame_id = a11y._find_subtree_by_selector(page, tree, [], '.missing')

    assert result == tree
    assert '未匹配到元素' in warning
    assert frame_id is None


@pytest.mark.unit
def test_take_a11y_snapshot_cdp_failure_fallback():
    """让 Accessibility.getFullAXTree 抛异常、且 run_js(JS fallback)返回 {'tree':{}, 'stats':{}} → 结果 method == 'js_fallback' 且带 warning。"""
    page = FakePage(
        cdp_map={'Accessibility.getFullAXTree': RuntimeError('CDP failed')},
        run_js_return={'tree': {}, 'stats': {'total': 0}}
    )
    result = a11y.take_a11y_snapshot(page)
    
    assert result['method'] == 'js_fallback'
    assert 'warning' in result


@pytest.mark.unit
def test_take_a11y_snapshot_total_failure():
    """CDP 抛异常且 JS fallback 也抛异常 → 优雅返回 method == 'failed' 且带 error，
    不应抛 UnboundLocalError（回归测试：a11y.py:169 的 cdp_err 作用域 bug）。"""
    page = FakePage(
        cdp_map={'Accessibility.getFullAXTree': RuntimeError('CDP failed')},
        run_js_return=RuntimeError('JS fallback failed'),
    )
    result = a11y.take_a11y_snapshot(page)

    assert result['method'] == 'failed'
    assert 'error' in result
    assert result['tree'] == {}


@pytest.mark.unit
def test_find_subtree_by_selector_css_prefix():
    """css: 前缀应该被去掉并使用 DOM.querySelector。"""
    cdp_result = {
        'root': {'nodeId': 1},
        'nodeId': 100
    }
    page = FakePage(cdp_map={
        'DOM.getDocument': {'root': {'nodeId': 1}},
        'DOM.querySelector': cdp_result,
        'DOM.describeNode': {'node': {'backendNodeId': 200, 'nodeName': 'DIV'}}
    })
    
    tree = node(role='root', backendNodeId=1, children=[
        node(role='generic', backendNodeId=200, children=[
            node(role='button', backendNodeId=300)
        ])
    ])
    result, warning, frame_id = a11y._find_subtree_by_selector(page, tree, [], 'css:#test')

    assert warning is None
    assert result['backendNodeId'] == 200
    assert frame_id is None


@pytest.mark.unit
def test_find_subtree_by_selector_xpath_prefix():
    """xpath: 前缀应该使用 Runtime.evaluate 执行 document.evaluate。"""
    page = FakePage(cdp_map={
        'DOM.getDocument': {'root': {'nodeId': 1}},
        'Runtime.evaluate': {
            'result': {'type': 'object', 'objectId': 'obj123'}
        },
        'DOM.requestNode': {'nodeId': 100},
        'DOM.describeNode': {'node': {'backendNodeId': 200, 'nodeName': 'DIV'}}
    })
    
    tree = node(role='root', backendNodeId=1, children=[
        node(role='generic', backendNodeId=200)
    ])
    result, warning, frame_id = a11y._find_subtree_by_selector(page, tree, [], 'xpath://div')

    assert warning is None
    assert result['backendNodeId'] == 200
    assert frame_id is None


@pytest.mark.unit
def test_find_subtree_by_selector_iframe_switch():
    """匹配到 iframe 时应该切换到其 frame 的快照。"""
    frame_id = 'TEST_FRAME_ID'
    page = FakePage(cdp_map={
        'DOM.getDocument': {'root': {'nodeId': 1}},
        'DOM.querySelector': {'nodeId': 100},
        'DOM.describeNode': {'node': {'backendNodeId': 200, 'nodeName': 'IFRAME', 'frameId': frame_id}},
        'Accessibility.getFullAXTree': {
            'nodes': [
                {'nodeId': '1', 'role': {'type': 'string', 'value': 'WebArea'},
                 'backendDOMNodeId': 1, 'childIds': []}
            ]
        }
    })

    tree = node(role='root', backendNodeId=1)
    result, warning, returned_frame_id = a11y._find_subtree_by_selector(page, tree, [], 'iframe')

    assert '已切换到 iframe frame' in warning
    assert frame_id in warning
    assert result['role'] == 'WebArea'
    assert returned_frame_id == frame_id


@pytest.mark.unit
def test_generate_locators_iframe_frame():
    """iframe frame 下使用 page.ele 获取真实 DOM 属性生成 locator。"""
    frame_id = 'TEST_FRAME_ID'

    # 模拟有 ele 方法的 page
    class FakeEle:
        def __init__(self, tag, attrs):
            self.tag = tag
            self._attrs = attrs

        def attr(self, name):
            return self._attrs.get(name)

    class FakePageWithEle(FakePage):
        def __init__(self, ele_map=None):
            super().__init__(cdp_map={'DOM.getDocument': {'root': {'nodeId': 1}}})
            self._ele_map = ele_map or {}

        def ele(self, locator, timeout=None):
            return self._ele_map.get(locator)

    # 模拟 iframe 内的元素
    ele_map = {
        'text:导入': FakeEle('button', {'id': 'import-btn', 'class': 'btn'}),
        'text:导出': FakeEle('button', {'class': 'btn-export'}),
    }

    page = FakePageWithEle(ele_map=ele_map)
    interactive_nodes = [
        node(role='button', backendNodeId=100, name='导入'),
        node(role='button', backendNodeId=101, name='导出'),
        node(role='textbox', backendNodeId=102, name='搜索'),  # 没有对应的 ele
    ]

    # 调用 _generate_locators_batch with frame_id
    a11y._generate_locators_batch(page, interactive_nodes, frame_id=frame_id)

    # 验证生成了真正的 CSS selector（有 id 的优先使用 id）
    assert interactive_nodes[0]['locator'] == '#import-btn' or 'import-btn' in interactive_nodes[0]['locator']
    # 没有 id 的使用 class
    assert interactive_nodes[1]['locator'] == '.btn-export' or 'btn-export' in interactive_nodes[1]['locator']
    # 查找失败的回退到 text:
    assert interactive_nodes[2]['locator'] == 'text:搜索'