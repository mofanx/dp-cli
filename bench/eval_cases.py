"""eval 集——与训练种子（bench_needle.CASES[:34]）来源级隔离的独立样本。

纪律：本文件的 case 永不进 seed_train*。holdout=原 8 条+新造 30+
（不同场景构造：多语言/长列表/元决策/否定语义/相近干扰项）。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from bench_needle import CASES  # 复用后 8 条 holdout

def _mk(cid, goal, action, ref, title, url, opts):
    return {'id': cid, 'goal': goal,
            'expect': {'action': action, 'ref': ref},
            'url': url, 'title': title,
            'options': [{'ref': i+1, 'role': r, 'name': n}
                        for i, (r, n) in enumerate(opts)]}

NEW = [
    # ── 相近干扰项（同 role 多候选消歧）──
    _mk('n01', '点击删除', 'click', 3, '管理后台', 'https://admin.x/users',
        [('button', '编辑'), ('button', '查看'), ('button', '删除'), ('button', '导出')]),
    _mk('n02', 'click the last pagination button', 'click', 6, 'List', 'https://x/list',
        [('link', '1'), ('link', '2'), ('link', '3'), ('link', '4'), ('link', '5'), ('link', 'Next →')]),
    _mk('n03', '保存为草稿', 'click', 2, 'Editor', 'https://x/edit',
        [('button', '发布'), ('button', '保存草稿'), ('button', '预览'), ('button', '丢弃')]),
    _mk('n04', 'open the third tab "Billing"', 'click', 3, 'Console', 'https://x/c',
        [('tab', 'Overview'), ('tab', 'Usage'), ('tab', 'Billing'), ('tab', 'Settings')]),
    _mk('n05', '选择收货地址二', 'click', 2, 'Checkout', 'https://x/co',
        [('radio', '公司地址'), ('radio', '家'), ('radio', '学校'), ('button', '继续')]),
    _mk('n06', 'click English', 'click', 3, 'Lang', 'https://x/lang',
        [('link', '简体中文'), ('link', '繁體中文'), ('link', 'English'), ('link', '日本語')]),
    _mk('n07', '筛选本月订单', 'click', 4, 'Orders', 'https://x/ord',
        [('button', '今天'), ('button', '本周'), ('button', '上月'), ('button', '本月'), ('button', '全部')]),
    _mk('n08', 'download the CSV report', 'click', 2, 'Reports', 'https://x/r',
        [('button', 'Download PDF'), ('button', 'Download CSV'), ('button', 'Download Excel')]),
    # ── type/fill 类 ──
    _mk('n09', '在搜索框输入价格区间', 'type', 2, 'Shop', 'https://x/s',
        [('textbox', '关键词'), ('textbox', '价格区间'), ('button', '筛选')]),
    _mk('n10', 'enter the coupon code', 'type', 4, 'Cart', 'https://x/cart',
        [('textbox', 'Quantity'), ('textbox', 'Address'), ('textbox', 'Phone'), ('textbox', 'Coupon code')]),
    _mk('n11', '填写备注信息', 'type', 3, 'Order', 'https://x/o',
        [('textbox', '联系人'), ('textbox', '电话'), ('textbox', '备注'), ('button', '提交')]),
    _mk('n12', 'type the second password confirmation', 'type', 2, 'Signup', 'https://x/su',
        [('textbox', 'Password'), ('textbox', 'Confirm password'), ('button', 'Sign up')]),
    # ── select 类 ──
    _mk('n13', '选择排序方式：价格从低到高', 'select', 1, 'Sort', 'https://x/srt',
        [('combobox', '排序方式'), ('button', '重置')]),
    _mk('n14', 'pick shipping method dropdown', 'select', 3, 'Ship', 'https://x/sh',
        [('radio', 'Standard'), ('radio', 'Express'), ('combobox', 'Shipping method')]),
    # ── 长列表（10+ 元素）──
    _mk('n15', '点第 9 个导航项「监控」', 'click', 9, 'Ops', 'https://x/ops',
        [('link', x) for x in ['总览','主机','容器','网络','存储','日志','告警','审计','监控','设置']]),
    _mk('n16', 'scroll down for more items', 'scroll_down', None, 'Feed', 'https://x/f',
        [('link', 'Item A'), ('link', 'Item B'), ('link', 'Item C')]),
    _mk('n17', '点返回回到上个页面', 'back', None, 'Detail', 'https://x/d',
        [('link', '相关推荐'), ('button', '收藏')]),
    _mk('n18', '等它加载好再继续', 'wait', None, 'Loading', 'https://x/l',
        [('progressbar', 'loading')]),
    # ── 元决策（goal 暗示已完成/受阻）──
    _mk('n19', '查看是否下单成功了', 'done', None, '订单成功 - #12345', 'https://x/ok',
        [('link', '查看订单'), ('button', '继续购物')]),
    _mk('n20', '页面被验证码拦截', 'blocked', None, 'Verify you are human', 'https://x/v',
        [('checkbox', "I'm not a robot"), ('button', 'Verify')]),
    _mk('n21', '确认已退出登录', 'done', None, 'Logged out - 首页', 'https://x/out',
        [('button', '登录'), ('link', '注册')]),
    # ── 否定/排除语义 ──
    _mk('n22', '点除了"取消"以外的确认按钮', 'click', 1, 'Dialog', 'https://x/dg',
        [('button', '确定'), ('button', '取消')]),
    _mk('n23', 'do NOT click delete, click archive instead', 'click', 2, 'Mail', 'https://x/m',
        [('button', 'Delete'), ('button', 'Archive'), ('button', 'Mark read')]),
    # ── 深层语义（goal 与 name 无字面重叠）──
    _mk('n24', '我要结账', 'click', 3, 'Cart', 'https://x/ct',
        [('link', '继续购物'), ('button', '清空'), ('button', '去结算')]),
    _mk('n25', 'sign me out', 'click', 1, 'Profile', 'https://x/p',
        [('button', 'Log out'), ('button', 'Edit profile')]),
    _mk('n26', '放大图片查看', 'click', 4, 'Gallery', 'https://x/g',
        [('button', '上一张'), ('button', '下一张'), ('button', '全屏'), ('button', '放大')]),
    _mk('n27', '给这篇文章点赞', 'click', 2, 'Post', 'https://x/post',
        [('button', '评论'), ('button', '👍 赞'), ('button', '转发')]),
    _mk('n28', 'adjust volume to maximum', 'select', 2, 'Settings', 'https://x/set',
        [('checkbox', 'Mute'), ('combobox', 'Volume'), ('button', 'Apply')]),
    _mk('n29', '导出全部数据为 JSON', 'click', 5, 'Data', 'https://x/da',
        [('button', '导出 CSV'), ('button', '导出 XML'), ('button', '导出 PDF'), ('button', '打印'), ('button', '导出 JSON')]),
    _mk('n30', '点击"忘记密码"', 'click', 2, 'Login', 'https://x/lg',
        [('button', '登录'), ('link', '忘记密码'), ('link', '注册账号')]),
]

NEW2 = [
    # ── meta 决策桶（done/blocked/wait/back——miss n17-n21 同型）──
    _mk('m01', '等页面加载完再说', 'wait', None, 'Loading…', 'https://x/w1',
        [('progressbar', '0%'), ('link', 'Skip')]),
    _mk('m02', '确认表单提交成功了', 'done', None, '提交成功', 'https://x/d1',
        [('button', '提交'), ('link', '返回')]),
    _mk('m03', '触发了人机验证被拦', 'blocked', None, 'Please verify', 'https://x/b1',
        [('checkbox', 'Verify me'), ('button', 'Continue')]),
    _mk('m04', '回到上一个页面', 'back', None, 'Article detail', 'https://x/bk1',
        [('link', 'More articles'), ('button', 'Share')]),
    _mk('m05', '已经买到票了确认下', 'done', None, '购票成功', 'https://x/d2',
        [('button', '查看订单'), ('link', '再抢一张')]),
    _mk('m06', '处理中请稍等', 'wait', None, 'Processing', 'https://x/w2',
        [('progressbar', 'uploading'), ('button', 'Cancel')]),
    _mk('m07', '先回到搜索列表页', 'back', None, 'Item #432', 'https://x/bk2',
        [('button', 'Buy'), ('link', 'Related')]),
    _mk('m08', '号被封了没法操作', 'blocked', None, 'Account suspended', 'https://x/b2',
        [('button', 'Appeal'), ('link', 'Terms')]),
    # ── deep_semantic 桶（goal 与 name 无字面重叠——n05/n24/n27 同型）──
    _mk('m09', '结算购物车', 'click', 4, 'Cart page', 'https://x/ds1',
        [('link', 'Continue shopping'), ('button', 'Clear'), ('link', 'Wishlist'), ('button', '去结算')]),
    _mk('m10', '给这条动态点个赞', 'click', 3, 'Feed item', 'https://x/ds2',
        [('button', '💬'), ('button', '↗'), ('button', '👍'), ('link', '…')]),
    _mk('m11', '把地址二设成收货地址', 'click', 2, 'Address picker', 'https://x/ds3',
        [('radio', '公司'), ('radio', '家'), ('radio', '学校'), ('button', '确认')]),
    _mk('m12', 'reply to this thread', 'click', 3, 'Thread', 'https://x/ds4',
        [('link', 'Report'), ('link', 'Quote'), ('textbox', 'Write a reply'), ('button', 'Subscribe')]),
    _mk('m13', '搜飞东京的航班', 'type', 2, 'Flights', 'https://x/ds5',
        [('textbox', 'From'), ('textbox', 'To'), ('textbox', 'Depart'), ('button', 'Search')]),
    _mk('m14', '把字体调大一号', 'click', 3, 'Reader', 'https://x/ds6',
        [('button', 'A-'), ('button', 'Theme'), ('button', 'A+'), ('button', 'Print')]),
    _mk('m15', 'mute the notification', 'click', 4, 'Alerts', 'https://x/ds7',
        [('button', 'Snooze'), ('button', 'Settings'), ('button', 'Clear all'), ('button', 'Bell off')]),
    _mk('m16', '开通会员', 'click', 2, 'Pricing', 'https://x/ds8',
        [('button', 'Compare plans'), ('button', 'Subscribe'), ('link', 'FAQ')]),
    _mk('m17', '把这篇存起来以后看', 'click', 2, 'Article', 'https://x/ds9',
        [('button', 'Share'), ('button', '🔖'), ('button', 'Font')]),
    # ── long_list / ordinal 桶（n15 同型）──
    _mk('m18', '点第 12 个分类', 'click', 12, 'Catalog', 'https://x/ll1',
        [('link', x) for x in ['Books','Travel','Mystery','Historical','Sequential','Classics',
                                'Philosophy','Romance','Womens','Fiction','Crime','Humor','Sports','Music','Cook']]),
    _mk('m19', '选第 5 行的编辑按钮', 'click', 5, 'Table', 'https://x/ll2',
        [('button', 'Edit#1'), ('button', 'Edit#2'), ('button', 'Edit#3'), ('button', 'Edit#4'),
         ('button', 'Edit#5'), ('button', 'Edit#6'), ('button', 'Edit#7')]),
    _mk('m20', 'scroll to see more products', 'scroll_down', None, 'Shop grid', 'https://x/ll3',
        [('link', 'P1'), ('link', 'P2'), ('link', 'P3'), ('link', 'P4'), ('link', 'P5'),
         ('link', 'P6'), ('link', 'P7'), ('link', 'P8'), ('link', 'P9'), ('link', 'P10')]),
    _mk('m21', '点最后一页', 'click', 8, 'Pager', 'https://x/ll4',
        [('link', '1'), ('link', '2'), ('link', '3'), ('link', '4'), ('link', '5'),
         ('link', '6'), ('link', '7'), ('link', '8')]),
    # ── near_distractor 桶（n01/n30 同型——近义候选干扰）──
    _mk('m22', '导出报表', 'click', 4, 'Report menu', 'https://x/nd1',
        [('button', '编辑'), ('button', '查看'), ('button', '删除'), ('button', '导出')]),
    _mk('m23', '找回我的密码', 'click', 3, 'Sign in', 'https://x/nd2',
        [('button', '登录'), ('link', '注册账号'), ('link', '忘记密码')]),
    _mk('m24', '把商品移出清单', 'click', 4, 'List item', 'https://x/nd3',
        [('button', '移入收藏'), ('button', '置顶'), ('button', '分享'), ('button', '移除')]),
    _mk('m25', '去注册一个账号', 'click', 3, 'Landing', 'https://x/nd4',
        [('button', 'Log in'), ('link', 'Forgot password'), ('link', 'Sign up')]),
    # ── select/combobox 桶（n13/n14/n28 同型）──
    _mk('m26', '选下拉的第二个选项', 'select', 1, 'Chooser', 'https://x/sl1',
        [('combobox', 'Options'), ('button', 'Go')]),
    _mk('m27', '把排序改成最新', 'select', 2, 'Sort', 'https://x/sl2',
        [('button', 'Filters'), ('combobox', 'Sort by'), ('link', 'Reset')]),
    _mk('m28', '挑一个发货方式', 'select', 3, 'Shipping', 'https://x/sl3',
        [('radio', 'Pickup'), ('radio', 'Mail'), ('combobox', 'Delivery method')]),
    # ── 否定/混合语义 ──
    _mk('m29', '别点删除点归档', 'click', 3, 'Inbox', 'https://x/mx1',
        [('button', 'Delete'), ('button', 'Spam'), ('button', 'Archive'), ('button', 'Read')]),
    _mk('m30', '选不是"取消"的那个', 'click', 1, 'Confirm', 'https://x/mx2',
        [('button', '确定'), ('button', '取消'), ('button', '关闭')]),
]

EVAL_CASES = CASES[34:] + NEW + NEW2   # 8 holdout + 30 + 30 = 68

META_IDS = {c['id'] for c in EVAL_CASES if c['expect']['action'] in
            ('done', 'blocked', 'wait', 'back', 'scroll_down', 'scroll_up')}
# meta 决策：action 非元素 ref 语义——供评估分桶（elem/meta 分开报）

if __name__ == '__main__':
    import json
    print(json.dumps({'n_eval': len(EVAL_CASES),
                      'from_train_holdout': 8, 'new': len(NEW)}))
