"""序数定位确定性规则——_meta_decide ordinal 分支定向测试。

eval-120 x-系归因：第N个/第N行/最后一页类 goal 模型拿不到位置信号，
走确定性规则直解 ref，不进 CrossEncoder。
"""
from dp_cli.agent import _meta_decide, _ordinal_n


def _st(n=10):
    return {'options': [{'ref': i, 'role': 'link', 'name': f'item{i}'}
                        for i in range(1, n + 1)],
            'state': {'title': '', 'url': ''},
            'epoch': 'x', 'truncated': False}


def test_ordinal_n_parse():
    assert _ordinal_n('x', '点第 7 个菜单项') == 7
    assert _ordinal_n('x', '编辑第二行数据') == 2
    assert _ordinal_n('x', '打开第 10 个告警') == 10
    assert _ordinal_n('x', '翻到第 3 页') == 3
    assert _ordinal_n('x', '选最后一页的页码') == -1
    assert _ordinal_n('the last item', 'the last item') == -1
    assert _ordinal_n('pick the 2nd row', 'pick the 2nd row') == 2
    assert _ordinal_n('pick the third', 'pick the third') == 3


def test_ordinal_no_signal():
    assert _ordinal_n('x', '点击保存按钮') is None
    assert _ordinal_n('x', '第三次重试') is None          # 次数非位置
    assert _ordinal_n('x', '这批发票全部作废') is None


def test_meta_ordinal_click_ref():
    r = _meta_decide(_st(10), '点第 7 个菜单项')
    assert r['action'] == 'click' and r['ref'] == 7


def test_meta_ordinal_last():
    r = _meta_decide(_st(6), '选最后一页的页码')
    assert r['action'] == 'click' and r['ref'] == 6


def test_meta_ordinal_type_dispatch():
    r = _meta_decide(_st(5), '填第 2 个输入框')
    assert r['action'] == 'type' and r['ref'] == 2


def test_meta_ordinal_out_of_range_falls_through():
    # 序数超出候选数 → 不短路，回退常规判定链
    r = _meta_decide(_st(3), '点第 9 个菜单项')
    assert r is None or r.get('ref') != 9


def test_meta_ordinal_cn_number():
    assert _ordinal_n('x', '点第六位成员') == 6
    assert _ordinal_n('x', '第三位') == 3
