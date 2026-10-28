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


# ── 多步 goal 截首步（x34/x41 miss 归因修复）────────────────

def test_first_step_multistep():
    """先X再Y/X完再Y/X然后Y/then 截首步——reranker 只吃首步目标。"""
    from dp_cli.agent import _first_step
    assert _first_step('先填手机号再点下一步') == '填手机号'
    assert _first_step('填完验证码再登录') == '填完验证码'
    assert _first_step('先选城市再提交') == '选城市'
    assert _first_step('登录然后退出') == '登录'
    assert _first_step('login then logout') == 'login'


def test_first_step_no_false_cut():
    """'再见/再次'词内'再'不误切；单步 goal 原样返回。"""
    from dp_cli.agent import _first_step
    assert _first_step('点再见按钮') == '点再见按钮'
    assert _first_step('再次确认') == '再次确认'
    assert _first_step('点登录') == '点登录'
    assert _first_step('先登录') == '先登录'


def test_blocked_form_field_not_wall():
    """候选含'验证码'输入框≠人机墙——表单字段不误判 blocked。"""
    from dp_cli.agent import _meta_decide
    st = {'options': [{'ref': 1, 'role': 'textbox', 'name': '手机'},
                      {'ref': 2, 'role': 'textbox', 'name': '验证码'}],
          'state': {'title': 'Login', 'url': 'https://x'},
          'epoch': 'x', 'truncated': False}
    assert _meta_decide(st, '填完验证码再登录') is None


def test_blocked_real_wall_still_hits():
    """真人机验证特征（title/元素名）仍判 blocked。"""
    from dp_cli.agent import _meta_decide
    st = {'options': [{'ref': 1, 'role': 'button', 'name': '安全验证'}],
          'state': {'title': 'Please verify you are human', 'url': 'https://x'},
          'epoch': 'x', 'truncated': False}
    r = _meta_decide(st, '继续')
    assert r and r['action'] == 'blocked'
