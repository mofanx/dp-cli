# -*- coding:utf-8 -*-
"""候选召回覆盖修复回归——v26b 真机归因三补：

1. clickable_extras 并入 _page_state 候选（a11y 漏收的真交互元素）
2. 空 name DOM 回填（裸 input/无 label 控件语义可达）
3. max_depth=None 全深度 + recall_pool 消灭 (top_k,阈值] 截断死区
"""
import pytest

from dp_cli import agent
import dp_cli.snapshot.a11y as a11y


class _P:
    url = 'https://x/'
    title = 't'

    def run_cdp(self, method, **kw):          # 默认不支持——per-test 覆盖
        raise RuntimeError('cdp unsupported')


def _mk(tree_children, extras=None):
    return {'tree': {'role': 'WebArea', 'children': tree_children},
            'clickable_extras': extras or []}


def _btn(i, name='btn', **kw):
    return {'role': 'button', 'name': name, 'value': '',
            'backendNodeId': i, 'locator': f'css:#b{i}',
            'children': [], **kw}


def _stub(monkeypatch, snap, page_cls=_P):
    monkeypatch.setattr(a11y, 'take_a11y_snapshot', lambda p, **kw: snap)
    saved = {}
    monkeypatch.setattr(agent, 'save_refs', lambda s, u, r: saved.update(r))
    monkeypatch.setattr(agent, 'load_session', lambda s: {})
    monkeypatch.setattr(agent, 'save_session', lambda s, d: None)
    return saved


@pytest.mark.unit
def test_page_state_merges_clickable_extras(monkeypatch):
    """a11y 未覆盖的 extras（div tab/a 漏网 link）进候选；
    无名 extras 滤掉；tag→role 映射 a→link、div→clickable。"""
    snap = _mk([_btn(1, '登录'), _btn(2, '注册')],
               extras=[
                   {'tag': 'a', 'label': '下一页',
                    'locator': 'css:a.next', 'backendNodeId': 91},
                   {'tag': 'div', 'label': '', 'text': '大国外交',
                    'locator': 'css:.ch', 'backendNodeId': 92},
                   {'tag': 'div', 'label': '', 'text': '',
                    'locator': 'css:.empty', 'backendNodeId': 93},
                   {'tag': 'button', 'label': '百度一下',
                    'locator': 'css:#su', 'backendNodeId': 94},
               ])
    _stub(monkeypatch, snap)
    st = agent._page_state(_P(), 's', recall_pool=True)
    names = [(o['role'], o['name']) for o in st['options']]
    assert ('link', '下一页') in names
    assert ('clickable', '大国外交') in names
    assert ('button', '百度一下') in names
    assert not any(n == '' for _, n in names)          # 无名 extras 已滤
    # extras 追加在 a11y 之后——a11y ref 序号不变
    assert st['options'][0]['name'] == '登录'
    assert len(st['options']) == 5
    assert st['options_total'] == 5


@pytest.mark.unit
def test_page_state_extra_refs_locator(monkeypatch):
    """extras 候选 refs 用 clickable detect 的 locator——click ref:N 可达。"""
    snap = _mk([_btn(1, '登录')],
               extras=[{'tag': 'div', 'text': '频道',
                        'locator': 'css:.ch-tab', 'backendNodeId': 92}])
    saved = _stub(monkeypatch, snap)
    st = agent._page_state(_P(), 's')
    assert saved['2']['locator'] == 'css:.ch-tab'
    assert saved['2']['name'] == '频道'


@pytest.mark.unit
def test_page_state_fills_empty_names(monkeypatch):
    """空 name 候选 → DOM.resolveNode+callFunctionOn 回填名字；
    非空 name 节点不发 CDP。"""
    calls = []

    class P(_P):
        def run_cdp(self, method, **kw):
            calls.append(method)
            if method == 'DOM.resolveNode':
                return {'object': {'objectId': 'o1'}}
            if method == 'Runtime.callFunctionOn':
                return {'result': {'value': ' Dark mode '}}
            raise RuntimeError(method)

    snap = _mk([_btn(1, '登录'), _btn(2, ''), _btn(3, '')])
    _stub(monkeypatch, snap)
    st = agent._page_state(P(), 's')
    assert st['options'][1]['name'] == 'Dark mode'
    assert st['options'][2]['name'] == 'Dark mode'
    # resolveNode 只发了 2 次（2 个空 name）——非空节点零开销
    assert calls.count('DOM.resolveNode') == 2


@pytest.mark.unit
def test_page_state_fill_names_cdp_failure_tolerant(monkeypatch):
    """CDP 全挂不阻塞快照——空 name 保持原样。"""
    class P(_P):
        def run_cdp(self, method, **kw):
            raise RuntimeError('cdp dead')

    snap = _mk([_btn(1, ''), _btn(2, 'ok')])
    _stub(monkeypatch, snap)
    st = agent._page_state(P(), 's')
    assert len(st['options']) == 2
    assert st['options'][0]['name'] == ''


@pytest.mark.unit
def test_dom_name_js_machine_name_fallback():
    """_DOM_NAME_JS 末级兜底：name/id 机器属性拆词——站点 a11y
    全缺（gov.cn 搜索框 name="headSearchword" 零命名源）时给
    语义寻址留钩子。断言级别存在+顺序（在 img alt 之后、return 前）
    +拆词正则+长度/字母门控。"""
    js = agent._DOM_NAME_JS
    # 末级在 img alt 之后、return 之前
    i_img = js.index("el.tagName === 'IMG'")
    i_attr = js.index("attr('name') || (el.id || '')")
    i_ret = js.index('return (n || ')
    assert i_img < i_attr < i_ret
    # camelCase/蛇形拆词正则
    assert "([a-z0-9])([A-Z])" in js
    # 门控：≥3 字符且含字母——防 name="t"/name="q" 噪声名
    assert 'raw.length >= 3' in js


@pytest.mark.unit
def test_page_state_full_depth_snapshot(monkeypatch):
    """max_depth=None 全深度——depth=12 截断丢深层交互子树
    （baidu #kw/#su 实测只在全深度树出现）。"""
    seen = {}

    def _snap(p, max_depth=None, **kw):
        seen['depth'] = max_depth
        return _mk([_btn(1, 'x')])

    monkeypatch.setattr(agent, 'save_refs', lambda s, u, r: None)
    monkeypatch.setattr(agent, 'load_session', lambda s: {})
    monkeypatch.setattr(agent, 'save_session', lambda s, d: None)
    monkeypatch.setattr(a11y, 'take_a11y_snapshot', _snap)
    agent._page_state(_P(), 's')
    assert seen['depth'] is None


@pytest.mark.unit
def test_page_state_dead_zone_no_truncation(monkeypatch):
    """total∈(top_k, 阈值]：recall_pool=True 全量保留——旧 guard
    只在 total>阈值 扩池，25 候选被砍到 20 尾部永不可达。"""
    snap = _mk([_btn(i, f'b{i}') for i in range(25)])
    _stub(monkeypatch, snap)
    st = agent._page_state(_P(), 's', max_opts=20, recall_pool=True)
    assert len(st['options']) == 25
    assert st['truncated'] is False
    st2 = agent._page_state(_P(), 's', max_opts=20, recall_pool=False)
    assert len(st2['options']) == 20 and st2['truncated'] is True


@pytest.mark.unit
def test_page_state_menu_roles_collected(monkeypatch):
    """menuitemcheckbox/menuitemradio 进候选——role 集与 a11y 对齐。"""
    snap = _mk([
        {'role': 'menuitemcheckbox', 'name': '自动连播', 'value': '',
         'backendNodeId': 1, 'locator': 'css:#m1', 'children': []},
        {'role': 'menuitemradio', 'name': '标清', 'value': '',
         'backendNodeId': 2, 'locator': 'css:#m2', 'children': []},
        {'role': 'StaticText', 'name': 'ignore me', 'value': '',
         'backendNodeId': 3, 'children': []},
    ])
    _stub(monkeypatch, snap)
    st = agent._page_state(_P(), 's')
    names = [o['name'] for o in st['options']]
    assert '自动连播' in names and '标清' in names
    assert 'ignore me' not in names
