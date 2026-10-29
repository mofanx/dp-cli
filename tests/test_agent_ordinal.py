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


def test_blocked_content_words_not_wall():
    """资讯站候选名/标题含'机器人/拦截/无人机/盾'类内容词不判 blocked
    ——P2 backlog：cnblogs'人形机器人'标题致全页短路、ithome 11 goals
    零 reranker 产出、连 hard_case 采集都被掐掉。"""
    from dp_cli.agent import _meta_decide
    st = {'options': [{'ref': 1, 'role': 'link', 'name': '人形机器人最新进展'},
                      {'ref': 2, 'role': 'link', 'name': '无人机拦截系统'},
                      {'ref': 3, 'role': 'link', 'name': '矛盾文学奖揭晓'}],
          'state': {'title': '科技资讯 - 首页', 'url': 'https://news/x'},
          'epoch': 'x', 'truncated': False}
    assert _meta_decide(st, '点开第一条新闻') is not None or \
        _meta_decide(st, '点开第一条新闻').get('action') != 'blocked'


def test_blocked_captcha_element_still_hits():
    """候选名为真挑战控件（人机验证/安全验证）仍判 blocked。"""
    from dp_cli.agent import _meta_decide
    st = {'options': [{'ref': 1, 'role': 'checkbox', 'name': '人机验证'},
                      {'ref': 2, 'role': 'button', 'name': '继续'}],
          'state': {'title': 'Checkout', 'url': 'https://x'},
          'epoch': 'x', 'truncated': False}
    r = _meta_decide(st, '继续')
    assert r and r['action'] == 'blocked'


# ── 词界/元素覆盖/序数选项/action 重判（eval-120 n08/c10/m26/m12 归因）──

def test_kw_hit_ascii_word_boundary():
    """ASCII 词左词界——'load'⊂'download'/'ok'⊂'Tokyo' 不命中，
    词形后缀（loading/waited）仍命中。"""
    from dp_cli.agent import _kw_hit
    assert not _kw_hit('load', 'download the csv report', 'x')
    assert not _kw_hit('ok', 'search flights to tokyo', 'x')
    assert _kw_hit('load', 'wait for the page to load', 'x')
    assert _kw_hit('load', 'still loading', 'x')          # 形态后缀
    assert _kw_hit('wait', 'waited a bit', 'x')
    assert _kw_hit('加载', '等页面加载完', '等页面加载完')   # zh 仍子串


def test_download_not_wait():
    """n08 归因——'download' 内 'load' 不误判 wait。"""
    from dp_cli.agent import _meta_decide
    st = {'options': [{'ref': 1, 'role': 'button', 'name': 'Download PDF'},
                      {'ref': 2, 'role': 'button', 'name': 'Download CSV'},
                      {'ref': 3, 'role': 'button', 'name': 'Download Excel'}],
          'state': {'title': 'Reports', 'url': 'https://x/r'},
          'epoch': 'x', 'truncated': False}
    assert _meta_decide(st, 'download the CSV report') is None


def test_wait_still_hits():
    """真等待意图仍判 wait——词界不误伤正常形态。"""
    from dp_cli.agent import _meta_decide
    st = {'options': [{'ref': 1, 'role': 'button', 'name': 'Retry'}],
          'state': {'title': 'Page', 'url': 'https://x'},
          'epoch': 'x', 'truncated': False}
    r = _meta_decide(st, '等页面加载完再说')
    assert r and r['action'] == 'wait'
    r = _meta_decide(st, 'wait for loading to finish')
    assert r and r['action'] == 'wait'


def test_scroll_kw_covered_by_element_not_meta():
    """c10 归因——滚动词同时是候选元素名（'查看全部 12 条回复'按钮）
    → 意图落在元素上，不短路 scroll。"""
    from dp_cli.agent import _meta_decide
    st = {'options': [{'ref': 1, 'role': 'link', 'name': 'alice'},
                      {'ref': 2, 'role': 'button', 'name': '点赞'},
                      {'ref': 3, 'role': 'button',
                       'name': '查看全部 12 条回复'}],
          'state': {'title': 'Chat', 'url': 'https://chat.example/t/5'},
          'epoch': 'x', 'truncated': False}
    assert _meta_decide(st, '展开回复线程查看全部回复') is None


def test_scroll_intent_uncovered_still_hits():
    """滚动词无元素承载时仍判 scroll——guard 不误伤真滚动意图。"""
    from dp_cli.agent import _meta_decide
    st = {'options': [{'ref': 1, 'role': 'link', 'name': 'item1'},
                      {'ref': 2, 'role': 'link', 'name': 'item2'}],
          'state': {'title': 'List', 'url': 'https://x'},
          'epoch': 'x', 'truncated': False}
    r = _meta_decide(st, 'scroll down for more items')
    assert r and r['action'] == 'scroll_down'
    r = _meta_decide(st, 'scroll to the top')
    assert r and r['action'] == 'scroll_up'


def test_ordinal_option_selects_combobox():
    """m26 归因——'选下拉的第二个选项'的 N 是 option 序号非 ref，
    select 作用在 combobox 本体。"""
    from dp_cli.agent import _meta_decide
    st = {'options': [{'ref': 1, 'role': 'combobox', 'name': 'Options'},
                      {'ref': 2, 'role': 'button', 'name': 'Go'}],
          'state': {'title': 'Chooser', 'url': 'https://x/sl1'},
          'epoch': 'x', 'truncated': False}
    r = _meta_decide(st, '选下拉的第二个选项')
    assert r and r['action'] == 'select' and r['ref'] == 1


def test_ordinal_option_en_and_listbox():
    """英文序数选项 + listbox 同样走 select 本体。"""
    from dp_cli.agent import _meta_decide
    st = {'options': [{'ref': 1, 'role': 'button', 'name': 'Apply'},
                      {'ref': 2, 'role': 'listbox', 'name': 'Size'}],
          'state': {'title': 'Pick', 'url': 'https://x'},
          'epoch': 'x', 'truncated': False}
    r = _meta_decide(st, 'pick the third option')
    assert r and r['action'] == 'select' and r['ref'] == 2


def test_ordinal_option_no_combobox_falls_back():
    """序数+选项但无下拉元素 → 普通元素序数 click（不误伤）。"""
    from dp_cli.agent import _meta_decide
    r = _meta_decide(_st(6), '点第 2 个选项')
    assert r and r['action'] == 'click' and r['ref'] == 2
    r = _meta_decide(_st(10), '点第 7 个菜单项')     # '菜单项'≠'选项'
    assert r and r['action'] == 'click' and r['ref'] == 7


def test_goal_action_textbox_no_intent_click():
    """m12 归因——textbox 目标无输入意图 → click（focus 前置）非 type。"""
    from dp_cli.agent import _goal_action
    assert _goal_action('reply to this thread', 'textbox') == 'click'
    assert _goal_action('open the reply box', 'searchbox') == 'click'
    # 输入意图词在场仍判 type
    assert _goal_action('type a comment', 'textbox') == 'type'
    assert _goal_action('search flights to Tokyo', 'searchbox') == 'type'
    assert _goal_action('把预算改成 5000', 'spinbutton') == 'type'
    assert _goal_action('备注栏写加急', 'textbox') == 'type'
    assert _goal_action('填邀请码加入团队', 'textbox') == 'type'
    # 非 type 族角色不受影响
    assert _goal_action('reply to this thread', 'combobox') == 'select'
    assert _goal_action('reply to this thread', 'button') == 'click'
