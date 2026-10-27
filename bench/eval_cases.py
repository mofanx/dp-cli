"""eval 集——与训练种子（bench_needle.CASES[:34]）来源级隔离的独立样本。

纪律：本文件的 case 永不进 seed_train*。holdout=原 8 条+新造 30+
（不同场景构造：多语言/长列表/元决策/否定语义/相近干扰项）。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from bench_needle import CASES  # 复用后 8 条 holdout

def _mk(cid, goal, action, ref, title, url, opts):
    out = []
    for i, o in enumerate(opts):
        r, n = o[0], o[1]
        e = {'ref': i + 1, 'role': r, 'name': n}
        if len(o) > 2:                     # (role, name, opt_values)
            e['opt_values'] = o[2]
        out.append(e)
    return {'id': cid, 'goal': goal,
            'expect': {'action': action, 'ref': ref},
            'url': url, 'title': title, 'options': out}

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

NEW3 = [
    # ── deep_semantic（业务页形态——goal 与 name 零字面重叠）──
    _mk('x01', '帮我开一个新工单', 'click', 1, 'Ticket', 'https://x/tk',
        [('button', 'Create Ticket'), ('button', '编辑'), ('button', '导出'), ('button', '筛选')]),
    _mk('x02', '把库存不足的货标出来', 'click', 2, 'Stock', 'https://x/st',
        [('button', '全部'), ('button', '低库存筛选'), ('button', '导出报表')]),
    _mk('x03', '这批发票全部作废', 'click', 3, 'Invoice', 'https://x/inv',
        [('button', '批量开票'), ('button', '批量下载'), ('button', '批量冲红')]),
    _mk('x04', '驳回小李的转正申请', 'click', 2, 'HR Review', 'https://x/hr',
        [('button', '批准转正'), ('button', '不予通过'), ('link', '查看档案')]),
    _mk('x05', '把这篇草稿推上线', 'click', 2, 'CMS', 'https://x/cms',
        [('button', '预览'), ('button', '发布到线上'), ('button', '删除')]),
    _mk('x06', '给这位客户升个等级', 'click', 2, 'Member', 'https://x/mb',
        [('button', '冻结账号'), ('button', 'VIP 升级'), ('button', '发券')]),
    _mk('x07', '查这单物流到哪了', 'click', 3, 'Delivery', 'https://x/dl',
        [('link', '修改地址'), ('link', '取消订单'), ('link', '运单轨迹')]),
    _mk('x08', '把差旅费报掉', 'click', 2, 'Expense', 'https://x/exp',
        [('button', '查看历史'), ('button', '新建报销单'), ('button', '审批设置')]),
    _mk('x09', '给我换到下周的班', 'click', 2, 'Schedule', 'https://x/sch',
        [('button', '请假'), ('button', '申请调班'), ('button', '导出排班表')]),
    _mk('x10', '把这批邮件发出去', 'click', 2, 'Mailer', 'https://x/mail',
        [('button', '保存草稿'), ('button', '开始投递'), ('button', '预览模板')]),
    _mk('x11', '这个线索派给华东团队', 'click', 3, 'CRM', 'https://x/crm',
        [('button', '分配至-华北'), ('button', '分配至-华南'), ('button', '分配至-华东')]),
    _mk('x12', '把这条告警压掉', 'click', 2, 'Alerts', 'https://x/al',
        [('button', 'Escalate'), ('button', 'Acknowledge'), ('button', 'Mute 1h')]),
    _mk('x13', '把这个服务重启一下', 'click', 3, 'K8s Pod', 'https://x/k8s',
        [('button', 'Logs'), ('button', 'Scale'), ('button', 'Rolling restart'), ('button', 'Delete')]),
    _mk('x14', '把文档分享给团队', 'click', 2, 'Docs', 'https://x/doc',
        [('button', '复制链接'), ('button', '邀请协作者'), ('button', '移出文库')]),
    _mk('x15', '把这个候选人约面', 'click', 3, 'ATS', 'https://x/ats',
        [('button', '查看简历'), ('button', '淘汰'), ('button', '安排面试'), ('button', '发 offer')]),
    # ── select/combobox ──
    # combobox 第三元=opt_values——模拟生产 snapshot_cmd 注入后的形态
    # （refs.name 并入 `|` 分隔选项值，ranker 拿到 goal↔选项值 lexical hook）
    _mk('x16', '切换语言到日语', 'select', 2, 'Prefs', 'https://x/p1',
        [('button', '保存'), ('combobox', 'Language', ['English', '中文', '日本語']), ('link', '取消')]),
    _mk('x17', '按销量排商品', 'select', 1, 'Catalog', 'https://x/p2',
        [('combobox', '排序：默认', ['默认', '销量', '价格', '上新']), ('button', '筛选')]),
    _mk('x18', '选美元结算', 'select', 3, 'Billing', 'https://x/p3',
        [('radio', '人民币'), ('radio', '港币'), ('combobox', 'Currency', ['USD', 'EUR', 'JPY'])]),
    _mk('x19', '把时区调成东京', 'select', 1, 'Settings', 'https://x/p4',
        [('combobox', 'Timezone', ['UTC', 'Asia/Tokyo', 'Asia/Shanghai']), ('button', '重置默认')]),
    _mk('x20', '换成深色主题', 'select', 2, 'Appearance', 'https://x/p5',
        [('button', '预览'), ('combobox', 'Theme', ['Light', 'Dark', 'Auto']), ('link', '恢复默认')]),
    _mk('x21', '导出格式选 PDF', 'select', 1, 'Export', 'https://x/p6',
        [('combobox', 'Export as', ['CSV', 'PDF', 'JSON']), ('button', '下载')]),
    _mk('x22', 'pick a US region', 'select', 2, 'Deploy', 'https://x/p7',
        [('radio', 'EU'), ('combobox', 'Region', ['US-East', 'US-West', 'EU-Central']), ('button', 'Deploy')]),
    _mk('x23', '把字号调成大号', 'select', 1, 'Reader', 'https://x/p8',
        [('combobox', 'Font size', ['Small', 'Medium', 'Large']), ('button', 'A-'), ('button', 'A+')]),
    # ── 长列表序数 ──
    _mk('x24', '点第 7 个菜单项', 'click', 7, 'Admin', 'https://x/l1',
        [('link', x) for x in ['仪表盘','订单','商品','客户','库存','营销','报表','设置','日志','权限']]),
    _mk('x25', '选最后一页的页码', 'click', 6, 'Pager', 'https://x/l2',
        [('link', '1'), ('link', '2'), ('link', '3'), ('link', '4'), ('link', '5'), ('link', '»')]),
    _mk('x26', '打开表格第 4 行的详情', 'click', 4, 'Tickets', 'https://x/l3',
        [('link', '工单#1001'), ('link', '工单#1002'), ('link', '工单#1003'), ('link', '工单#1004'), ('link', '工单#1005')]),
    _mk('x27', '点第 15 个分类', 'click', 15, 'Catalog', 'https://x/l4',
        [('link', f'分类{i}') for i in range(1, 21)]),
    _mk('x28', '选中字母表里以 D 开头那个', 'click', 4, 'Filter', 'https://x/l5',
        [('link', ch) for ch in 'ABCDEFGHIJ']),
    _mk('x29', '点第 6 位成员', 'click', 6, 'Team', 'https://x/l6',
        [('link', x) for x in ['张三','李四','王五','赵六','孙七','周八','吴九','郑十']]),
    _mk('x30', '翻到第 3 页看更多', 'click', 3, 'Results', 'https://x/l7',
        [('link', '上一页'), ('link', '1'), ('link', '3'), ('link', '4'), ('link', '下一页')]),
    _mk('x31', '打开第 10 个告警', 'click', 10, 'Monitor', 'https://x/l8',
        [('link', f'ALERT-{i:03d}') for i in range(1, 13)]),
    # ── 多步表单字段 ──
    _mk('x32', '填完收货人名字再提交', 'type', 2, 'Checkout', 'https://x/f1',
        [('textbox', '地址'), ('textbox', '收件人'), ('button', '提交订单')]),
    _mk('x33', '在优惠码框输入新人券', 'type', 3, 'Pay', 'https://x/f2',
        [('textbox', '卡号'), ('textbox', '有效期'), ('textbox', 'Promo code'), ('button', '支付')]),
    _mk('x34', '先填手机号再点下一步', 'type', 1, 'Signup', 'https://x/f3',
        [('textbox', '手机'), ('textbox', '短信验证码'), ('button', '下一步')]),
    _mk('x35', '把预算改成 5000', 'type', 2, 'Ads', 'https://x/f4',
        [('textbox', '投放名称'), ('textbox', '预算上限'), ('button', '保存')]),
    _mk('x36', '备注栏写加急', 'type', 3, 'Order', 'https://x/f5',
        [('textbox', '品名'), ('textbox', '数量'), ('textbox', '备注'), ('button', '下单')]),
    _mk('x37', '填信用卡后四位', 'type', 1, 'Verify', 'https://x/f6',
        [('textbox', '卡号后四位'), ('textbox', '持卡人姓名'), ('button', '验证')]),
    _mk('x38', '在税号栏填公司税号', 'type', 2, 'Invoice', 'https://x/f7',
        [('textbox', '发票抬头'), ('textbox', 'Tax ID'), ('button', '保存')]),
    _mk('x39', '填邀请码加入团队', 'type', 1, 'Join', 'https://x/f8',
        [('textbox', '邀请码'), ('button', '加入'), ('link', '没有邀请码？')]),
    _mk('x40', '输入数量 3 件', 'type', 2, 'Cart', 'https://x/f9',
        [('textbox', '商品名'), ('spinbutton', '数量'), ('button', '更新购物车')]),
    _mk('x41', '填完验证码再登录', 'type', 2, 'Login', 'https://x/fa',
        [('textbox', '用户名'), ('textbox', '验证码'), ('button', '登录')]),
    # ── 相近干扰项（真实行内操作/工具栏形态）──
    _mk('x42', '查看这张订单', 'click', 2, 'Orders', 'https://x/d1',
        [('button', '编辑'), ('link', '详情'), ('button', '删除'), ('button', '退款')]),
    _mk('x43', '删掉这条记录', 'click', 2, 'Table', 'https://x/d2',
        [('button', 'Edit'), ('button', 'Del'), ('button', 'View'), ('button', 'Clone')]),
    _mk('x44', 'approve this expense', 'click', 2, 'Review', 'https://x/d3',
        [('button', 'Review'), ('button', 'Approve'), ('button', 'Reject'), ('button', 'Comment')]),
    _mk('x45', '关闭第一条通知', 'click', 1, 'Inbox', 'https://x/d4',
        [('button', '×'), ('button', '×'), ('button', '×')]),
    _mk('x46', '编辑第二行数据', 'click', 2, 'Grid', 'https://x/d5',
        [('button', '编辑'), ('button', '编辑'), ('button', '编辑'), ('button', '编辑')]),
    _mk('x47', '点右侧的箭头', 'click', 2, 'Carousel', 'https://x/d6',
        [('button', '‹'), ('button', '›'), ('button', '⋮')]),
    _mk('x48', '用邮箱登录而不是手机', 'click', 2, 'Sign in', 'https://x/d7',
        [('link', '手机登录'), ('link', '邮箱登录'), ('link', '扫码登录')]),
    _mk('x49', 'export the selected rows', 'click', 2, 'Data', 'https://x/d8',
        [('button', 'Import'), ('button', 'Export'), ('button', 'Print'), ('button', 'Sync')]),
    # ── 图标/混合语义 ──
    _mk('x50', 'toggle dark mode on', 'click', 2, 'UI', 'https://x/e1',
        [('button', '☼'), ('button', '☾'), ('button', 'A+')]),
    _mk('x51', '标记已读全部消息', 'click', 2, 'Notify', 'https://x/e2',
        [('button', '删除全部'), ('button', '全部标记已读'), ('button', '刷新')]),
    _mk('x52', 'star this repository', 'click', 2, 'Repo', 'https://x/e3',
        [('button', 'Watch'), ('button', '★ Star'), ('button', 'Fork')]),
]

EVAL_CASES = CASES[34:] + NEW + NEW2 + NEW3   # 8 holdout + 30 + 30 + 52 = 120

META_IDS = {c['id'] for c in EVAL_CASES if c['expect']['action'] in
            ('done', 'blocked', 'wait', 'back', 'scroll_down', 'scroll_up')}
# meta 决策：action 非元素 ref 语义——供评估分桶（elem/meta 分开报）

if __name__ == '__main__':
    import json
    print(json.dumps({'n_eval': len(EVAL_CASES),
                      'from_train_holdout': 8, 'new': len(NEW)}))
