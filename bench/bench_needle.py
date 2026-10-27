#!/usr/bin/env python3
"""Needle-3 decide 引擎 benchmark（非主线代码，实验产物）。

协议对齐 dp_cli/agent.py 的 decide 外部协议：
  请求 = state(url/title) + options[{ref,role,name}] + goal
  应答 = {action, target, done, confidence, ...}

工具面 = _ACTIONS 九个动作一对一映射成 needle tool。
指标：
  a) 直接决策准确率（action+ref 全对 / action-only / ref-only）
  b) top-3 recall——needle 无 probs 输出，定义为主选+suppressed_calls 合集
  c) confidence 校准——阈值分桶看能否做 act/confirm/refuse 门
"""
import json
import sys
import time
from pathlib import Path

OUT = Path(__file__).parent / 'needle_results.json'

# ── 用例集：手工标注 ground truth（options 格式同 agent._page_state 输出）──

CASES = [
    # ── click 类 ──
    {'id': 'c01', 'goal': '点登录', 'expect': {'action': 'click', 'ref': 2},
     'url': 'https://example.com', 'title': 'Example - 首页',
     'options': [{'ref': 1, 'role': 'link', 'name': '首页'},
                 {'ref': 2, 'role': 'button', 'name': '登录'},
                 {'ref': 3, 'role': 'link', 'name': '注册'}]},
    {'id': 'c02', 'goal': 'click the submit button', 'expect': {'action': 'click', 'ref': 5},
     'url': 'https://form.io/order', 'title': 'Order form',
     'options': [{'ref': 1, 'role': 'textbox', 'name': 'Name'},
                 {'ref': 2, 'role': 'textbox', 'name': 'Email'},
                 {'ref': 3, 'role': 'checkbox', 'name': 'Agree to terms'},
                 {'ref': 4, 'role': 'button', 'name': 'Cancel'},
                 {'ref': 5, 'role': 'button', 'name': 'Submit'}]},
    {'id': 'c03', 'goal': '打开“更多”菜单', 'expect': {'action': 'click', 'ref': 3},
     'url': 'https://news.site', 'title': 'News',
     'options': [{'ref': 1, 'role': 'link', 'name': '头条'},
                 {'ref': 2, 'role': 'link', 'name': '体育'},
                 {'ref': 3, 'role': 'menuitem', 'name': '更多'},
                 {'ref': 4, 'role': 'link', 'name': '财经'}]},
    {'id': 'c04', 'goal': 'close the cookie banner', 'expect': {'action': 'click', 'ref': 1},
     'url': 'https://shop.example', 'title': 'Shop',
     'options': [{'ref': 1, 'role': 'button', 'name': 'Accept all cookies'},
                 {'ref': 2, 'role': 'textbox', 'name': 'Search products'},
                 {'ref': 3, 'role': 'link', 'name': 'Privacy policy'}]},
    {'id': 'c05', 'goal': '进入设置页', 'expect': {'action': 'click', 'ref': 6},
     'url': 'https://app.example/dashboard', 'title': 'Dashboard',
     'options': [{'ref': 1, 'role': 'link', 'name': '概览'},
                 {'ref': 2, 'role': 'link', 'name': '报表'},
                 {'ref': 3, 'role': 'link', 'name': '团队'},
                 {'ref': 4, 'role': 'button', 'name': '新建项目'},
                 {'ref': 5, 'role': 'link', 'name': '帮助'},
                 {'ref': 6, 'role': 'link', 'name': '设置'}]},
    # ── type 类 ──
    {'id': 't01', 'goal': '搜索 Python', 'expect': {'action': 'type', 'ref': 3},
     'url': 'https://engine.example', 'title': 'Search',
     'options': [{'ref': 1, 'role': 'link', 'name': 'logo'},
                 {'ref': 2, 'role': 'button', 'name': '搜索'},
                 {'ref': 3, 'role': 'searchbox', 'name': '搜索'},
                 {'ref': 4, 'role': 'link', 'name': '高级'}]},
    {'id': 't02', 'goal': 'fill in the email field', 'expect': {'action': 'type', 'ref': 2},
     'url': 'https://app.example/login', 'title': 'Sign in',
     'options': [{'ref': 1, 'role': 'textbox', 'name': 'Username'},
                 {'ref': 2, 'role': 'textbox', 'name': 'Email'},
                 {'ref': 3, 'role': 'textbox', 'name': 'Password'},
                 {'ref': 4, 'role': 'button', 'name': 'Sign in'}]},
    {'id': 't03', 'goal': '在数量框输入 2', 'expect': {'action': 'type', 'ref': 4},
     'url': 'https://cart.example', 'title': 'Cart',
     'options': [{'ref': 1, 'role': 'link', 'name': '商品A'},
                 {'ref': 2, 'role': 'button', 'name': '删除'},
                 {'ref': 3, 'role': 'button', 'name': '结算'},
                 {'ref': 4, 'role': 'spinbutton', 'name': '数量'}]},
    {'id': 't04', 'goal': 'enter city name Shanghai', 'expect': {'action': 'type', 'ref': 1},
     'url': 'https://weather.example', 'title': 'Weather',
     'options': [{'ref': 1, 'role': 'combobox', 'name': 'City'},
                 {'ref': 2, 'role': 'button', 'name': 'Go'},
                 {'ref': 3, 'role': 'link', 'name': 'Map'}]},
    # ── select 类 ──
    {'id': 's01', 'goal': '选择排序方式为最新', 'expect': {'action': 'select', 'ref': 2},
     'url': 'https://shop.example/list', 'title': 'Product list',
     'options': [{'ref': 1, 'role': 'textbox', 'name': '筛选'},
                 {'ref': 2, 'role': 'listbox', 'name': '排序方式'},
                 {'ref': 3, 'role': 'button', 'name': '筛选'}]},
    {'id': 's02', 'goal': 'pick country from dropdown', 'expect': {'action': 'select', 'ref': 3},
     'url': 'https://form.io/address', 'title': 'Address',
     'options': [{'ref': 1, 'role': 'textbox', 'name': 'Street'},
                 {'ref': 2, 'role': 'textbox', 'name': 'Zip'},
                 {'ref': 3, 'role': 'listbox', 'name': 'Country'},
                 {'ref': 4, 'role': 'button', 'name': 'Save'}]},
    # ── scroll 类（目标元素不在候选 → 该滚动）──
    {'id': 'sc01', 'goal': '找到并点击“加入我们”链接（页底）', 'expect': {'action': 'scroll_down', 'ref': None},
     'url': 'https://corp.example', 'title': 'Corp home',
     'options': [{'ref': 1, 'role': 'link', 'name': '产品'},
                 {'ref': 2, 'role': 'link', 'name': '解决方案'},
                 {'ref': 3, 'role': 'button', 'name': '试用'},
                 {'ref': 4, 'role': 'link', 'name': '新闻'}]},
    {'id': 'sc02', 'goal': 'see more reviews below', 'expect': {'action': 'scroll_down', 'ref': None},
     'url': 'https://shop.example/item/1', 'title': 'Item page',
     'options': [{'ref': 1, 'role': 'button', 'name': 'Add to cart'},
                 {'ref': 2, 'role': 'button', 'name': 'Buy now'},
                 {'ref': 3, 'role': 'link', 'name': 'Specifications'}]},
    {'id': 'sc03', 'goal': '回到页面顶部的导航栏', 'expect': {'action': 'scroll_up', 'ref': None},
     'url': 'https://blog.example/post', 'title': 'Long post',
     'options': [{'ref': 1, 'role': 'link', 'name': '上一篇'},
                 {'ref': 2, 'role': 'link', 'name': '下一篇'},
                 {'ref': 3, 'role': 'button', 'name': '评论'}]},
    # ── back 类 ──
    {'id': 'b01', 'goal': '返回上一页', 'expect': {'action': 'back', 'ref': None},
     'url': 'https://a.example/deep/page', 'title': 'Detail',
     'options': [{'ref': 1, 'role': 'link', 'name': '首页'},
                 {'ref': 2, 'role': 'button', 'name': '收藏'}]},
    # ── wait 类 ──
    {'id': 'w01', 'goal': '等待页面加载完成', 'expect': {'action': 'wait', 'ref': None},
     'url': 'https://slow.example', 'title': 'Loading...',
     'options': [{'ref': 1, 'role': 'progressbar', 'name': 'loading'}]},
    # ── done 类 ──
    {'id': 'd01', 'goal': '登录到账户（已显示“欢迎回来”）', 'expect': {'action': 'done', 'ref': None},
     'url': 'https://app.example/home', 'title': 'Home - 欢迎回来',
     'options': [{'ref': 1, 'role': 'link', 'name': '个人中心'},
                 {'ref': 2, 'role': 'button', 'name': '退出登录'}]},
    {'id': 'd02', 'goal': 'verify search results for Python are shown', 'expect': {'action': 'done', 'ref': None},
     'url': 'https://engine.example?q=Python', 'title': 'Python - Search results',
     'options': [{'ref': 1, 'role': 'link', 'name': 'Python.org'},
                 {'ref': 2, 'role': 'link', 'name': 'Python tutorial'},
                 {'ref': 3, 'role': 'searchbox', 'name': '搜索', 'current': 'Python'}]},
    {'id': 'd03', 'goal': '确认订单已提交成功', 'expect': {'action': 'done', 'ref': None},
     'url': 'https://shop.example/order/ok', 'title': '订单提交成功',
     'options': [{'ref': 1, 'role': 'link', 'name': '查看订单'},
                 {'ref': 2, 'role': 'button', 'name': '继续购物'}]},
    # ── blocked 类 ──
    {'id': 'x01', 'goal': '继续浏览商品（出现验证码弹窗）', 'expect': {'action': 'blocked', 'ref': None},
     'url': 'https://shop.example', 'title': '人机验证',
     'options': [{'ref': 1, 'role': 'checkbox', 'name': '我不是机器人'},
                 {'ref': 2, 'role': 'button', 'name': '验证'}]},
    # ── 歧义/干扰类（多个相似候选）──
    {'id': 'a01', 'goal': '点击“保存”按钮', 'expect': {'action': 'click', 'ref': 3},
     'url': 'https://editor.example/doc', 'title': 'Doc',
     'options': [{'ref': 1, 'role': 'button', 'name': '保存草稿'},
                 {'ref': 2, 'role': 'button', 'name': '另存为'},
                 {'ref': 3, 'role': 'button', 'name': '保存'},
                 {'ref': 4, 'role': 'button', 'name': '取消'}]},
    {'id': 'a02', 'goal': 'open the second article "Market Update"', 'expect': {'action': 'click', 'ref': 4},
     'url': 'https://news.example', 'title': 'News list',
     'options': [{'ref': 1, 'role': 'link', 'name': 'Breaking News'},
                 {'ref': 2, 'role': 'link', 'name': 'Tech Roundup'},
                 {'ref': 3, 'role': 'link', 'name': 'Sports Daily'},
                 {'ref': 4, 'role': 'link', 'name': 'Market Update'},
                 {'ref': 5, 'role': 'link', 'name': 'Weather'}]},
    {'id': 'a03', 'goal': '勾选“记住我”复选框', 'expect': {'action': 'click', 'ref': 4},
     'url': 'https://app.example/login', 'title': 'Login',
     'options': [{'ref': 1, 'role': 'textbox', 'name': '账号'},
                 {'ref': 2, 'role': 'textbox', 'name': '密码'},
                 {'ref': 3, 'role': 'button', 'name': '登录'},
                 {'ref': 4, 'role': 'checkbox', 'name': '记住我'}]},
    {'id': 'a04', 'goal': 'switch to the "Images" tab', 'expect': {'action': 'click', 'ref': 2},
     'url': 'https://engine.example?q=cat', 'title': 'cat - results',
     'options': [{'ref': 1, 'role': 'tab', 'name': 'All'},
                 {'ref': 2, 'role': 'tab', 'name': 'Images'},
                 {'ref': 3, 'role': 'tab', 'name': 'Videos'},
                 {'ref': 4, 'role': 'tab', 'name': 'News'}]},
    # ── 大候选集（15-20 项，目标靠后）──
    {'id': 'l01', 'goal': 'click "导出数据"', 'expect': {'action': 'click', 'ref': 12},
     'url': 'https://admin.example/reports', 'title': 'Admin',
     'options': [{'ref': i, 'role': 'link', 'name': n} for i, n in enumerate(
         ['概览', '用户', '权限', '审计', '账单', '订阅', 'API', 'Webhooks',
          '通知', '安全', '日志', '导出数据', '备份', '集成', '设置', '帮助'], 1)]},
    {'id': 'l02', 'goal': 'select the "December" option', 'expect': {'action': 'select', 'ref': 8},
     'url': 'https://report.example', 'title': 'Report builder',
     'options': [{'ref': i, 'role': ('listbox' if i == 8 else 'checkbox'), 'name': n}
                 for i, n in enumerate(
                     ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Month selector',
                      'Q1', 'Q2', 'Q3', 'Q4'], 1)]},
    {'id': 'l03', 'goal': '点第 18 项“下载全部”', 'expect': {'action': 'click', 'ref': 18},
     'url': 'https://files.example', 'title': 'Files',
     'options': [{'ref': i, 'role': 'checkbox' if i < 18 else 'button',
                  'name': f'file_{i:02d}.pdf' if i < 18 else '下载全部'}
                 for i in range(1, 19)]},
    # ── 中英混排/边缘 ──
    {'id': 'm01', 'goal': 'click 提交 to send the form', 'expect': {'action': 'click', 'ref': 3},
     'url': 'https://form.example/zh', 'title': '报名表',
     'options': [{'ref': 1, 'role': 'textbox', 'name': '姓名'},
                 {'ref': 2, 'role': 'textbox', 'name': '手机'},
                 {'ref': 3, 'role': 'button', 'name': '提交'}]},
    {'id': 'm02', 'goal': '拒绝个性化推荐弹窗', 'expect': {'action': 'click', 'ref': 2},
     'url': 'https://app.example', 'title': 'App',
     'options': [{'ref': 1, 'role': 'button', 'name': '接受全部'},
                 {'ref': 2, 'role': 'button', 'name': '仅必要'},
                 {'ref': 3, 'role': 'link', 'name': '了解更多'}]},
    {'id': 'm03', 'goal': 'play the video', 'expect': {'action': 'click', 'ref': 1},
     'url': 'https://video.example/v/9', 'title': 'Video',
     'options': [{'ref': 1, 'role': 'button', 'name': 'Play'},
                 {'ref': 2, 'role': 'button', 'name': 'Mute'},
                 {'ref': 3, 'role': 'slider', 'name': 'Seek'}]},
    {'id': 'm04', 'goal': '展开“高级选项”折叠面板', 'expect': {'action': 'click', 'ref': 5},
     'url': 'https://config.example', 'title': 'Config',
     'options': [{'ref': 1, 'role': 'textbox', 'name': '名称'},
                 {'ref': 2, 'role': 'checkbox', 'name': '启用'},
                 {'ref': 3, 'role': 'button', 'name': '取消'},
                 {'ref': 4, 'role': 'button', 'name': '保存'},
                 {'ref': 5, 'role': 'button', 'name': '高级选项'}]},
    # ── done vs action 边界（标题暗示但未完成）──
    {'id': 'e01', 'goal': '登录账户（页面仍显示登录表单）', 'expect': {'action': 'type', 'ref': 1},
     'url': 'https://app.example/login', 'title': 'Login',
     'options': [{'ref': 1, 'role': 'textbox', 'name': 'Username'},
                 {'ref': 2, 'role': 'textbox', 'name': 'Password'},
                 {'ref': 3, 'role': 'button', 'name': 'Sign in'}]},
    {'id': 'e02', 'goal': '退出登录', 'expect': {'action': 'click', 'ref': 2},
     'url': 'https://app.example/home', 'title': 'Home',
     'options': [{'ref': 1, 'role': 'link', 'name': '设置'},
                 {'ref': 2, 'role': 'button', 'name': '退出登录'},
                 {'ref': 3, 'role': 'link', 'name': '帮助'}]},
    {'id': 'e03', 'goal': 'click the disabled "下一步" button', 'expect': {'action': 'click', 'ref': 3},
     'url': 'https://wizard.example/s1', 'title': 'Step 1',
     'options': [{'ref': 1, 'role': 'textbox', 'name': '必填项'},
                 {'ref': 2, 'role': 'button', 'name': '上一步'},
                 {'ref': 3, 'role': 'button', 'name': '下一步'}]},
    # ── 补充 click/type 常规 ──
    {'id': 'c06', 'goal': 'add the item to wishlist', 'expect': {'action': 'click', 'ref': 2},
     'url': 'https://shop.example/item/7', 'title': 'Item',
     'options': [{'ref': 1, 'role': 'button', 'name': 'Add to cart'},
                 {'ref': 2, 'role': 'button', 'name': 'Add to wishlist'},
                 {'ref': 3, 'role': 'button', 'name': 'Share'}]},
    {'id': 'c07', 'goal': '下载 PDF 报告', 'expect': {'action': 'click', 'ref': 4},
     'url': 'https://report.example', 'title': 'Report',
     'options': [{'ref': 1, 'role': 'link', 'name': '打印'},
                 {'ref': 2, 'role': 'link', 'name': '分享'},
                 {'ref': 3, 'role': 'button', 'name': '刷新'},
                 {'ref': 4, 'role': 'button', 'name': '下载 PDF'}]},
    {'id': 't05', 'goal': 'type a comment in the reply box', 'expect': {'action': 'type', 'ref': 5},
     'url': 'https://forum.example/t/1', 'title': 'Thread',
     'options': [{'ref': 1, 'role': 'link', 'name': 'Reply #1'},
                 {'ref': 2, 'role': 'link', 'name': 'Reply #2'},
                 {'ref': 3, 'role': 'button', 'name': 'Like'},
                 {'ref': 4, 'role': 'button', 'name': 'Quote'},
                 {'ref': 5, 'role': 'textbox', 'name': 'Write a reply'}]},
    {'id': 'c08', 'goal': 'click radio option "Express shipping"', 'expect': {'action': 'click', 'ref': 2},
     'url': 'https://checkout.example', 'title': 'Checkout',
     'options': [{'ref': 1, 'role': 'radio', 'name': 'Standard shipping'},
                 {'ref': 2, 'role': 'radio', 'name': 'Express shipping'},
                 {'ref': 3, 'role': 'button', 'name': 'Continue'}]},
    {'id': 'c09', 'goal': '点“确定”关闭对话框', 'expect': {'action': 'click', 'ref': 1},
     'url': 'https://app.example', 'title': 'Dialog',
     'options': [{'ref': 1, 'role': 'button', 'name': '确定'},
                 {'ref': 2, 'role': 'button', 'name': '取消'}]},
    {'id': 't06', 'goal': 'search flights to Tokyo', 'expect': {'action': 'type', 'ref': 2},
     'url': 'https://travel.example', 'title': 'Flights',
     'options': [{'ref': 1, 'role': 'textbox', 'name': 'From'},
                 {'ref': 2, 'role': 'textbox', 'name': 'To'},
                 {'ref': 3, 'role': 'button', 'name': 'Search'}]},
    {'id': 'd04', 'goal': '确认文件已上传完成', 'expect': {'action': 'done', 'ref': None},
     'url': 'https://drive.example', 'title': '上传完成 - 3 files',
     'options': [{'ref': 1, 'role': 'link', 'name': 'report.pdf'},
                 {'ref': 2, 'role': 'button', 'name': '上传更多'}]},
    {'id': 'c10', 'goal': '展开回复线程查看全部回复', 'expect': {'action': 'click', 'ref': 3},
     'url': 'https://chat.example/t/5', 'title': 'Chat',
     'options': [{'ref': 1, 'role': 'link', 'name': 'alice'},
                 {'ref': 2, 'role': 'button', 'name': '点赞'},
                 {'ref': 3, 'role': 'button', 'name': '查看全部 12 条回复'}]},
]

# adapter 设计（实测迭代出的第三形态）：
#  v1 长上下文+通用动作工具 → 0%（不 emit call）
#  v2 extract 结构化抽取    → 幻觉 action/ref，无 grounding
#  v3 每元素一工具          → needle 原生 tool-choice 形态：
#     N 个 element_<k> 工具（desc 内嵌 role+name）+ 6 个动作工具，
#     模型选工具名即完成元素选择；action 由选中元素 role 经
#     _ROLE_ACTION 代码映射（与 julia 引擎路径同构，公平对位）

_ROLE_ACTION = {'textbox': 'type', 'combobox': 'select', 'searchbox': 'type',
                'spinbutton': 'type', 'listbox': 'select'}

_ACT_TOOLS = {
    'act_scroll_up': 'Scroll the page up.',
    'act_scroll_down': 'Scroll the page down.',
    'act_back': 'Navigate back to the previous page.',
    'act_wait': 'Wait for the page to load or settle.',
    'act_done': 'The goal is already complete; nothing to do.',
    'act_blocked': 'Progress is blocked (captcha, paywall, login wall).',
}
_ACT_MAP = {'act_scroll_up': 'scroll_up', 'act_scroll_down': 'scroll_down',
            'act_back': 'back', 'act_wait': 'wait',
            'act_done': 'done', 'act_blocked': 'blocked'}


def make_tools(case):
    """每用例一 agent——工具表=候选元素+动作工具（needle 原生 tool-choice）。"""
    tools = []
    for o in case['options']:
        tools.append({
            'name': f"element_{o['ref']}",
            'description': f"Select element {o['ref']}: {o['role']} "
                           f"'{o['name']}'",
            'parameters': {'type': 'object', 'properties': {}}})
    for name, desc in _ACT_TOOLS.items():
        tools.append({'name': name, 'description': desc,
                      'parameters': {'type': 'object', 'properties': {}}})
    return tools


def build_query(c):
    return f"Page '{c['title']}' ({c['url']}). Goal: {c['goal']}"


def predict(n, c):
    r = n.complete(build_query(c), max_new_tokens=512)
    calls = r.get('function_calls') or []
    suppressed = r.get('suppressed_calls') or []
    role_by_ref = {o['ref']: o['role'] for o in c['options']}

    def decode(name):
        if name in _ACT_MAP:
            return _ACT_MAP[name], None
        if name.startswith('element_'):
            try:
                ref = int(name.split('_', 1)[1])
            except ValueError:
                return None, None
            return _ROLE_ACTION.get(role_by_ref.get(ref, ''), 'click'), ref
        return None, None

    action, ref = (decode(calls[0].get('name'))
                   if calls else (None, None))
    candidates = {str(decode(cl.get('name')))
                  for cl in calls + suppressed}
    return {'action': action, 'ref': ref,
            'confidence': r.get('confidence'),
            'candidates': sorted(candidates),
            'raw_type': r.get('type')}


def score(case, pred):
    exp = case['expect']
    hit_full = (pred['action'] == exp['action']
                and (exp['ref'] is None or pred['ref'] == exp['ref']))
    hit_action = pred['action'] == exp['action']
    hit_ref = (exp['ref'] is None) or (pred['ref'] == exp['ref'])
    exp_pair = str((exp['action'], exp['ref']))
    topk = exp_pair in pred['candidates'] or hit_full
    return hit_full, hit_action, hit_ref, topk


def main():
    import needle
    rows = []
    for c in CASES:
        n = needle.Needle(
            tools=make_tools(c),
            system='You are a browser automation agent. Pick the element '
                   'that best advances the goal, or an action tool '
                   '(scroll/back/wait) when no element fits. Use '
                   'act_done when the goal is already achieved.',
            stateless=True)
        t0 = time.time()
        try:
            pred = predict(n, c)
        finally:
            n.close()
        lat = (time.time() - t0) * 1000
        full, act, ref, topk = score(c, pred)
        rows.append({'id': c['id'], 'goal': c['goal'], 'expect': c['expect'],
                     'pred': {k: pred[k] for k in ('action', 'ref', 'confidence')},
                     'candidates': pred['candidates'],
                     'hit_full': full, 'hit_action': act, 'hit_ref': ref,
                     'topk': topk, 'latency_ms': round(lat)})
        print(f"{c['id']}: exp={c['expect']} pred={pred['action']}:{pred['ref']} "
              f"conf={pred['confidence']} {'OK' if full else 'MISS'}")
    n_case = len(rows)
    acc = sum(r['hit_full'] for r in rows) / n_case
    acc_a = sum(r['hit_action'] for r in rows) / n_case
    acc_r = sum(r['hit_ref'] for r in rows) / n_case
    topk = sum(r['topk'] for r in rows) / n_case
    lats = sorted(r['latency_ms'] for r in rows)
    # confidence 校准：阈值分桶准确率
    conf_rows = [r for r in rows if r['pred']['confidence'] is not None]
    calib = []
    for th in (0.3, 0.5, 0.7, 0.85, 0.95):
        sel = [r for r in conf_rows if r['pred']['confidence'] >= th]
        if sel:
            calib.append({'th': th, 'n': len(sel),
                          'acc': round(sum(r['hit_full'] for r in sel) / len(sel), 4)})
    report = {
        'engine': 'needle3 (cactus-needle, local)',
        'n_cases': n_case,
        'accuracy_full': round(acc, 4),
        'accuracy_action': round(acc_a, 4),
        'accuracy_ref': round(acc_r, 4),
        'topk_recall': round(topk, 4),
        'latency_ms': {'p50': lats[len(lats) // 2],
                       'p95': lats[int(len(lats) * 0.95)]},
        'confidence_calibration': calib,
        'correct_conf': round(sum(r['pred']['confidence'] or 0
                                  for r in conf_rows if r['hit_full'])
                              / max(1, sum(1 for r in conf_rows if r['hit_full'])), 4),
        'wrong_conf': round(sum(r['pred']['confidence'] or 0
                                for r in conf_rows if not r['hit_full'])
                            / max(1, sum(1 for r in conf_rows if not r['hit_full'])), 4),
        'baseline_julia1': {'accuracy_full': 0.60, 'topk_recall': 1.0},
        'rows': rows}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('\n=== SUMMARY ===')
    print(f"full={acc:.1%} action={acc_a:.1%} ref={acc_r:.1%} "
          f"topk={topk:.1%} p50={lats[len(lats)//2]}ms")
    print('calib:', calib)
    print('report →', OUT)


if __name__ == '__main__':
    main()
