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

EVAL_CASES = CASES[34:] + NEW   # 原 holdout 8 + 新增 30 = 38

if __name__ == '__main__':
    import json
    print(json.dumps({'n_eval': len(EVAL_CASES),
                      'from_train_holdout': 8, 'new': len(NEW)}))
