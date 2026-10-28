#!/usr/bin/env python3
"""定向 hard-negative 教师标注生成（本 agent 即教师，无外部 API）。

背景：eval-120 miss 19 归因（m 系近干扰消歧 + x 系业务语义 + n 系否定/序号），
v23 均匀铺页 Δ=0 证"泛撒无效"——本轮只生成 miss 同形态的 hard-negative 组：
positives=正选、negatives=故意放高相似度干扰项（同名不同 role/近名不同义/
同桶兄弟/被否定项）。

泄漏铁律：miss case 的 goal/候选文本不得直接入训——全部换实体名/换页面/
换表述，只保同一判别难点。生成后跑 exact+子串泄漏检测（复用 merge_train._hit
对全部 120 条 eval goal）+ miss case 候选串零复用断言。
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from eval_cases import EVAL_CASES            # noqa: E402
from bench_engines import rich_name          # noqa: E402
from merge_train import _hit                 # noqa: E402

OUT = Path(__file__).parent / 'teacher_hardneg.jsonl'
OUT_ARR = Path(__file__).parent / 'teacher_hardneg.json'
OUT_Q = Path(__file__).parent / 'teacher_hardneg_quarantine.jsonl'

# 基线 miss 19（teacher_report v19/v23 记录）——其 goal 与候选文本零复用
MISS_IDS = {"t06", "c10", "n05", "n08", "n22", "m11", "m12", "m13", "m15",
            "m17", "m26", "m29", "m30", "x03", "x04", "x09", "x12", "x18", "x43",
            # 现役 v19 复跑 miss 增量（m14/m27/x01 替代 m15/m17/x09）
            "m14", "m27", "x01"}
EVAL_GOALS = {c['goal'].strip() for c in EVAL_CASES}
FORBIDDEN = {f"{o['role']} {rich_name(o)}"
             for c in EVAL_CASES if c['id'] in MISS_IDS
             for o in c['options']}

_rng = random.Random(20240824)
ROWS, QUAR = [], []


def mk(query, pos, negs, bucket, slug, note=''):
    """pos/negs: 'role name' 串。candidates=pos+negs 乱序。"""
    page = f'https://fixture.hardneg/{bucket}/{slug}'
    cands = [pos] + list(negs)
    _rng.shuffle(cands)
    return {'src': 'teacher_hardneg', 'prov': 'teacher_hardneg', 'page': page,
            'goal': query, 'query': query, 'positive': pos,
            'negatives': list(negs), 'candidates': cands,
            'teacher_note': note or bucket}


def quar(query, page_slug, reason):
    QUAR.append({'page': f'https://fixture.hardneg/quar/{page_slug}',
                 'query': query, 'reason': reason})


def B(role, name):
    return f'{role} {name}'


# ── A. 近名/同桶消歧（n08/x43 同型：同格式族兄弟、缩写行操作、同名异 role）──
def bucket_a():
    rows = []
    # A1 导出格式族——goal 指名格式，正选=对应格式，负=同族兄弟
    zh_fmt = [('报表导出成 {}', 'button 导出 {}'), ('把数据存一份 {} 的', 'button 导出 {}'),
              ('download this as {}', 'button Download {}'),
              ('导出 {} 格式的清单', 'link 导出 {}')]
    formats = [('YAML', 'TOML', 'Markdown'), ('TOML', 'XML', 'YAML'),
               ('Markdown', 'YAML', 'Plain text'), ('XML', 'TOML', 'JSON5'),
               ('JSON5', 'YAML', 'BSON'), ('SQLite', 'TOML', 'YAML'),
               ('PNG', 'SVG', 'Markdown'), ('SVG', 'PNG', 'YAML'),
               ('ZIP', 'TAR', 'GZIP'), ('TAR', 'ZIP', 'Markdown'),
               ('DOCX', 'ODT', 'Markdown'), ('ODT', 'DOCX', 'YAML'),
               ('BSON', 'JSON5', 'TOML'), ('INI', 'TOML', 'YAML')]
    for i, ((qt, pt), fmts) in enumerate(zip(zh_fmt * 4, formats)):
        pos_f, n1, n2 = fmts
        rows.append(mk(qt.format(pos_f), pt.format(pos_f),
                       [pt.format(n1), pt.format(n2), B('button', '打印预览')],
                       'near', f'a1-{i}', '导出格式族同前缀兄弟'))
    # A2 行内缩写操作——删→Del/Remove/Trash，负=Edit/View/Clone 类
    del_pos = ['button Del row', 'button Remove', 'button Trash',
               'button 删除行', 'button Del item', 'button Remove row',
               'button Drop', 'button 移除这条', 'button Erase']
    del_neg_sets = [
        [B('button', 'Edit row'), B('button', 'View details'), B('button', 'Clone row')],
        [B('button', 'Modify'), B('button', 'Inspect'), B('button', 'Duplicate')],
        [B('button', 'Rename'), B('button', 'Preview'), B('button', 'Copy row')]]
    del_goals = ['把这条日志删了', 'remove this task', '删除该用户记录',
                 'clear this entry', '把这条评论干掉', 'drop this row',
                 '移除这个设备', 'erase this draft', '把这条告警删了']
    for i, (g, p) in enumerate(zip(del_goals, del_pos)):
        rows.append(mk(g, p, del_neg_sets[i % 3], 'near', f'a2-{i}',
                       '行操作缩写：删→Del/Remove 同义'))
    # A3 同名异 role——goal 带"链接/按钮"形态限定词
    a3 = [
        ('点页面上那个叫"保存"的链接', B('link', '保存'),
         [B('button', '保存'), B('button', '另存为'), B('link', '保存草稿')]),
        ('press the "Export" button not the menu link', B('button', 'Export'),
         [B('link', 'Export'), B('button', 'Import'), B('link', 'Export history')]),
        ('点"帮助"那个文字链', B('link', '帮助'),
         [B('button', '帮助'), B('button', '联系客服'), B('link', '关于')]),
        ('click the Sign in link at the top', B('link', 'Sign in'),
         [B('button', 'Sign in'), B('link', 'Sign up'), B('button', 'Register')]),
        ('按"刷新"图标的那个按钮', B('button', '刷新'),
         [B('link', '刷新'), B('button', '重置'), B('link', '返回列表')]),
        ('open the 设置 entry link', B('link', '设置'),
         [B('button', '设置'), B('link', '设置向导'), B('button', '偏好')]),
        ('点底部"免责声明"链接', B('link', '免责声明'),
         [B('button', '免责声明'), B('link', '隐私政策'), B('button', '同意')]),
        ('hit the 提交 text link', B('link', '提交'),
         [B('button', '提交'), B('button', '提交全部'), B('link', '提交记录')]),
    ]
    for i, (q, p, ns) in enumerate(a3):
        rows.append(mk(q, p, ns, 'near', f'a3-{i}', '同名 button vs link 形态限定'))
    # A4 同前缀兄弟
    a4 = [
        ('批量归档这批邮件', B('button', '批量归档'),
         [B('button', '批量删除'), B('button', '批量导出'), B('button', '批量打标')]),
        ('批量审批这些请假单', B('button', '批量审批'),
         [B('button', '批量驳回'), B('button', '批量导出'), B('button', '批量转交')]),
        ('save all open tabs', B('button', 'Save all'),
         [B('button', 'Save draft'), B('button', 'Save & close'), B('button', 'Save as')]),
        ('提交这份报销', B('button', '提交报销'),
         [B('button', '提交审批'), B('button', '提交工单'), B('button', '暂存报销')]),
        ('新建一条入库单', B('button', '新建入库单'),
         [B('button', '新建出库单'), B('button', '新建盘点单'), B('button', '导入单据')]),
        ('open my draft folder', B('link', 'Drafts'),
         [B('link', 'Draft settings'), B('link', 'Sent'), B('button', 'New draft')]),
        ('把会话标记为未读', B('button', '标记未读'),
         [B('button', '标记已读'), B('button', '标记星标'), B('button', '全部已读')]),
        ('清空搜索历史', B('button', '清空历史'),
         [B('button', '清空缓存'), B('button', '清空回收站'), B('button', '导出历史')]),
    ]
    for i, (q, p, ns) in enumerate(a4):
        rows.append(mk(q, p, ns, 'near', f'a4-{i}', '同前缀兄弟候选'))
    # A5 登录页形态——找回/注册 vs 登录
    a5 = [
        ('密码丢了想重置', B('link', '重置密码'),
         [B('button', '登录'), B('link', '注册新账号'), B('link', '联系客服')]),
        ('reset my forgotten password', B('link', 'Reset password'),
         [B('button', 'Log in'), B('link', 'Create account'), B('link', 'Help')]),
        ('create a new account', B('link', 'Sign up'),
         [B('button', 'Log in'), B('link', 'Reset password'), B('link', 'Pricing')]),
        ('开个新账号', B('button', '立即注册'),
         [B('button', '登录'), B('link', '找回密码'), B('link', '企业版')]),
        ('账号锁了去解锁', B('link', '解锁账号'),
         [B('button', '登录'), B('link', '重置密码'), B('link', '申诉')]),
        ('use SSO to sign in', B('button', 'SSO 登录'),
         [B('button', 'Log in'), B('link', 'Forgot password'), B('link', 'Sign up')]),
    ]
    for i, (q, p, ns) in enumerate(a5):
        rows.append(mk(q, p, ns, 'near', f'a5-{i}', '登录页近名分流'))
    return rows


# ── B. 序号/地址类 goal 对多 entry（n05/m11/m19 同型）──
def bucket_b():
    rows = []
    # B1 地址选择器——序数指第 N 个地址 entry；名带序号的行+纯名行各半
    addr_sets = [
        ('收货地址挑第二个', B('radio', '地址2·父母家'),
         [B('radio', '地址1·单位'), B('radio', '地址3·出租屋'), B('button', '下一步')]),
        ('把第三顺位地址设为默认', B('radio', '默认地址3·门店'),
         [B('radio', '默认地址1·宿舍'), B('radio', '默认地址2·厂房'), B('button', '保存')]),
        ('delivery address: pick number two', B('radio', 'Address 2 - Office'),
         [B('radio', 'Address 1 - Home base'), B('radio', 'Address 3 - Warehouse'),
          B('button', 'Proceed')]),
        ('备用地址用第 4 条', B('radio', '备用4·朋友家'),
         [B('radio', '备用1·健身房'), B('radio', '备用2·图书馆'), B('button', '提交')]),
        ('收货地址用第二顺位那个', B('radio', '老家'),
         [B('radio', '单位收发室'), B('radio', '学校驿站'), B('button', '去支付')]),
        ('set shipping address to the second one', B('radio', 'Grandma\'s place'),
         [B('radio', 'Work address'), B('radio', 'PO Box'), B('button', 'Next')]),
        ('地址簿里选第 3 个收货点', B('radio', '公寓前台'),
         [B('radio', '公司前台'), B('radio', '驿站代收'), B('button', '保存地址')]),
        ('拿第 2 个常用地址收货', B('radio', '岳母家'),
         [B('radio', '写字楼'), B('radio', '姐姐家'), B('button', '选好')]),
        ('第 3 个地址当收货地', B('radio', '工作室'),
         [B('radio', '宿舍'), B('radio', '门市'), B('button', '使用')]),
        ('use my second saved address', B('radio', 'Saved addr 2 · Cabin'),
         [B('radio', 'Saved addr 1 · Loft'), B('radio', 'Saved addr 3 · Farm'),
          B('button', 'Apply')]),
    ]
    for i, (q, p, ns) in enumerate(addr_sets):
        rows.append(mk(q, p, ns, 'ord', f'b1-{i}', '序数地址 entry 消歧'))
    # B2 名带索引候选——'第N行'↔'#N' 可学映射
    idx_goals = [
        ('处理第 {n} 行那张单', 'button 处理#{n}', 'button 处理#{}'),
        ('open the row {n} editor', 'button Edit row {n}', 'button Edit row {}'),
        ('点第 {n} 个流程节点', 'link 节点-{n:02d}', 'link 节点-{:02d}'),
        ('resolve ticket number {n}', 'link T-{n} resolve', 'link T-{} resolve'),
        ('审批队列第 {n} 条', 'link 审批#{n}', 'link 审批#{}'),
        ('展开第 {n} 条评论', 'button 展开#{n}', 'button 展开#{}'),
        ('点检第 {n} 号工位', 'link 工位{n}号', 'link 工位{}号'),
        ('ack the {n}th alert in queue', 'button Ack #{n}', 'button Ack #{}'),
        ('看第 {n} 个采集任务', 'link 任务-{n:02d}', 'link 任务-{:02d}'),
        ('check item {n} off the list', 'button Done item {n}', 'button Done item {}'),
    ]
    nums = [3, 4, 6, 2, 5, 7, 3, 4, 6, 2]
    for i, ((qt, pt, nt), n) in enumerate(zip(idx_goals, nums)):
        pos = B(*pt.format(n=n).split(' ', 1))
        negs = [B(*nt.format(n + d).split(' ', 1)) for d in (-1, 1, 2)]
        rows.append(mk(qt.format(n=n), pos, negs, 'ord', f'b2-{i}',
                       '序数↔名内索引可学映射'))
    # B3 末位/首位——名带位置词
    b3 = [
        ('翻到最末一页', B('link', 'Last »'),
         [B('link', '1'), B('link', '2'), B('link', 'Next →'), B('link', '« First')]),
        ('跳到最后一屏结果', B('link', '末页'),
         [B('link', '上一页'), B('link', '3'), B('link', '下一页')]),
        ('go to the final page of results', B('link', 'Page 12 (last)'),
         [B('link', 'Page 1'), B('link', 'Page 2'), B('link', 'Next page')]),
        ('回到第一页从头看', B('link', '« 首页'),
         [B('link', '上一页'), B('link', '5'), B('link', '末页 »')]),
        ('back to the very first result page', B('link', 'First'),
         [B('link', 'Prev'), B('link', '4'), B('link', 'Last')]),
        ('拉到底看最后一条记录', B('link', '最旧一条'),
         [B('link', '最新一条'), B('link', '加载更多'), B('button', '刷新')]),
        ('open the earliest message', B('link', 'Oldest first'),
         [B('link', 'Newest first'), B('link', 'Load more'), B('button', 'Refresh')]),
    ]
    for i, (q, p, ns) in enumerate(b3):
        rows.append(mk(q, p, ns, 'ord', f'b3-{i}', '位置词候选（末页/首页）'))
    # B4 重复行组中的序数目标——名带行号区分
    b4 = [
        ('approve the third request in the list', B('button', 'Approve #3'),
         [B('button', 'Approve #1'), B('button', 'Approve #2'), B('button', 'Approve #4')]),
        ('关掉第 2 条提醒', B('button', '关闭提醒-02'),
         [B('button', '关闭提醒-01'), B('button', '关闭提醒-03'),
          B('button', '全部关闭')]),
        ('展开队列里第 5 个任务的日志', B('button', '日志·任务05'),
         [B('button', '日志·任务03'), B('button', '日志·任务04'),
          B('button', '日志·任务06')]),
    ]
    for i, (q, p, ns) in enumerate(b4):
        rows.append(mk(q, p, ns, 'ord', f'b4-{i}', '重复组内序号定位'))
    return rows


# ── C. 业务深语义映射（x03/x04/x09/x12/m15/m17/t06/m13 同型）──
def bucket_c():
    rows = []
    c1 = [  # 发票/财务——作废→红冲族
        ('把这批错票红冲掉', B('button', '红字冲销'),
         [B('button', '批量开具'), B('button', '批量导出'), B('button', '批量套打')]),
        ('void the whole batch of receipts', B('button', 'Void batch'),
         [B('button', 'Issue batch'), B('button', 'Download all'), B('button', 'Reprint')]),
        ('这几张发票都不要了，冲掉', B('button', '批量红冲'),
         [B('button', '批量开具'), B('button', '导出明细'), B('button', '批量查验')]),
        ('把开错的单整张作废', B('button', '作废单据'),
         [B('button', '补开单据'), B('button', '复制单据'), B('button', '归档')]),
        ('reverse these journal entries', B('button', 'Reverse entries'),
         [B('button', 'Post entries'), B('button', 'Export'), B('button', 'Approve')]),
        ('这张收据开错了，红冲', B('link', '红冲申请'),
         [B('link', '补开申请'), B('link', '下载'), B('button', '提交')]),
        ('invalidate all coupons in this batch', B('button', 'Invalidate batch'),
         [B('button', 'Issue batch'), B('button', 'Export codes'), B('button', 'Extend')]),
        ('报销单填错了整张报废', B('button', '整单作废'),
         [B('button', '修改重报'), B('button', '打印'), B('button', '提交审批')]),
    ]
    for i, (q, p, ns) in enumerate(c1):
        rows.append(mk(q, p, ns, 'biz', f'c1-{i}', '财务作废→红冲/作废族'))
    c2 = [  # 审批驳回族
        ('打回小王的加班申请', B('button', '退回申请'),
         [B('button', '同意加班'), B('button', '通过'), B('link', '查看详情')]),
        ('reject this onboarding request', B('button', 'Reject'),
         [B('button', 'Approve'), B('button', 'Defer'), B('link', 'View profile')]),
        ('不同意这次调薪幅度', B('button', '不予批准'),
         [B('button', '批准调薪'), B('button', '暂缓'), B('link', '薪酬档案')]),
        ('这条采购申请打回去重填', B('button', '驳回复核'),
         [B('button', '核准'), B('button', '转交'), B('link', '采购明细')]),
        ('decline the refund claim', B('button', 'Decline claim'),
         [B('button', 'Approve claim'), B('button', 'Escalate L2'), B('link', 'Claim docs')]),
        ('转正评审不通过', B('button', '评审驳回'),
         [B('button', '评审通过'), B('button', '延长试用'), B('link', '查看材料')]),
        ('把这个报价单退给销售重报', B('button', '退回重报'),
         [B('button', '接受报价'), B('button', '导出批次'), B('link', '历史报价')]),
        ('deny the access request', B('button', 'Deny'),
         [B('button', 'Grant'), B('button', 'Request info'), B('link', 'Policy')]),
    ]
    for i, (q, p, ns) in enumerate(c2):
        rows.append(mk(q, p, ns, 'biz', f'c2-{i}', '审批驳回→Reject/退回族'))
    c3 = [  # 告警压单族——压掉/认领→Ack
        ('把这条告警认领掉', B('button', 'Ack'),
         [B('button', 'Escalate L2'), B('button', 'Snooze 10m'), B('button', 'Forward')]),
        ('acknowledge this incident', B('button', 'Ack incident'),
         [B('button', 'Escalate L2'), B('button', 'Silence 30m'), B('button', 'Assign')]),
        ('这条异常我知晓了', B('button', '已知晓'),
         [B('button', '升级处理'), B('button', '静默1小时'), B('button', '转交')]),
        ('suppress this warning for now', B('button', 'Suppress'),
         [B('button', 'Escalate L2'), B('button', 'Mute 30m'), B('button', 'Details')]),
        ('先把这条报警压一压', B('button', '压单处理'),
         [B('button', '提级'), B('button', '挂起'), B('button', '通知值班')]),
        ('mark the alert as seen', B('button', 'Mark seen'),
         [B('button', 'Escalate L2'), B('button', 'Mute 30m'), B('button', 'Assign to')]),
        ('这批告警全部确认掉', B('button', '全部确认'),
         [B('button', '全部静默'), B('button', '批量升级'), B('button', '导出批次')]),
        ('silence the alert for an hour', B('button', 'Silence 1h'),
         [B('button', 'Ack'), B('button', 'Escalate L2'), B('button', 'Delete rule')]),
    ]
    for i, (q, p, ns) in enumerate(c3):
        rows.append(mk(q, p, ns, 'biz', f'c3-{i}', '告警压单→Ack/Suppress族'))
    c4 = [  # 排班/调度
        ('帮我跟同事换个班', B('button', '换班申请'),
         [B('button', '事假申请'), B('button', '加班申请'), B('button', '导出排班')]),
        ('swap my Friday shift with a teammate', B('button', 'Shift swap'),
         [B('button', 'Request leave'), B('button', 'Overtime'), B('button', 'Export')]),
        ('下周三的班想调走', B('button', '申请调换'),
         [B('button', '申请休息'), B('button', '打卡申诉'), B('button', '排班表')]),
        ('把早班换成晚班', B('button', '班次互换'),
         [B('button', '年假申请'), B('button', '调休'), B('button', '查看排班')]),
        ('trade shifts with the night crew', B('button', 'Trade shift'),
         [B('button', 'Take leave'), B('button', 'Swap days off'), B('button', 'Roster')]),
        ('这个月排班想整体往后挪', B('button', '排班调整'),
         [B('button', '调休申请'), B('button', '导出日历'), B('button', '复制排班')]),
    ]
    for i, (q, p, ns) in enumerate(c4):
        rows.append(mk(q, p, ns, 'biz', f'c4-{i}', '排班换班→调班族'))
    c5 = [  # 通知静音族
        ('把这个提醒弄成静音', B('button', '静音通知'),
         [B('button', '稍后提醒'), B('button', '设置'), B('button', '全部清除')]),
        ('silence this channel', B('button', 'Silence'),
         [B('button', 'Snooze 10m'), B('button', 'Preferences'), B('button', 'Leave')]),
        ('别让这个推送再响了', B('button', '关闭提醒音'),
         [B('button', '延迟提醒'), B('button', '通知设置'), B('button', '标为已读')]),
        ('turn off the ringer for this chat', B('button', 'Mute chat'),
         [B('button', 'Snooze 10m'), B('button', 'Chat settings'), B('button', 'Pin')]),
        ('这条告警声音关掉', B('button', '告警静音'),
         [B('button', '延迟处理'), B('button', '规则设置'), B('button', '清除')]),
        ('hush the reminder pings', B('button', 'Hush'),
         [B('button', 'Remind later'), B('button', 'Configure'), B('button', 'Dismiss all')]),
    ]
    for i, (q, p, ns) in enumerate(c5):
        rows.append(mk(q, p, ns, 'biz', f'c5-{i}', '通知静音→Mute/Bell族'))
    c6 = [  # 收藏/稍后看/图标语义
        ('这个帖子先码住回头再看', B('button', '☆ 收藏'),
         [B('button', '分享'), B('button', '转发'), B('button', '字体')]),
        ('save this article for later', B('button', 'Save for later'),
         [B('button', 'Share link'), B('button', 'Repost'), B('button', 'Print label')]),
        ('给这篇加个书签', B('button', '📑 书签'),
         [B('button', '分享链接'), B('button', '字号'), B('button', '夜间模式')]),
        ('bookmark this thread', B('button', 'Bookmark'),
         [B('button', 'Forward'), B('button', 'Link'), B('button', 'Follow')]),
        ('把这个视频放进稍后看清单', B('button', '稍后看'),
         [B('button', '分享'), B('button', '下载'), B('button', '举报')]),
        ('keep this recipe saved', B('button', '♡ Keep'),
         [B('button', 'Send'), B('button', 'Print recipe'), B('button', 'Rate')]),
    ]
    for i, (q, p, ns) in enumerate(c6):
        rows.append(mk(q, p, ns, 'biz', f'c6-{i}', '收藏稍后看→图标按钮'))
    c7 = [  # 工单/CRM/面试/物流/运维杂项
        ('帮客户开个售后工单', B('button', '新建工单'),
         [B('button', '编辑客户'), B('button', '导出记录'), B('button', '高级筛选')]),
        ('file a new support ticket', B('button', 'Open ticket'),
         [B('button', 'Edit ticket'), B('button', 'Export'), B('button', 'Filter')]),
        ('把这个候选人约个技术面', B('button', '预约面谈'),
         [B('button', '打开简历'), B('button', '淘汰此人'), B('button', '发录用信')]),
        ('schedule an onsite for this applicant', B('button', 'Schedule onsite'),
         [B('button', 'View resume'), B('button', 'Pass on'), B('button', 'Send offer')]),
        ('线索分给西南区团队', B('button', '分配至-西南'),
         [B('button', '分配至-华北'), B('button', '分配至-西北'), B('button', '分配至-华中')]),
        ('查下我这个包裹到哪了', B('link', '物流跟踪'),
         [B('link', '修改地址'), B('link', '取消订单'), B('link', '申请售后')]),
        ('track my shipment status', B('link', 'Track package'),
         [B('link', 'Change address'), B('link', 'Cancel order'), B('link', 'Invoice')]),
        ('把这个实例重启一下', B('button', 'Rolling reboot'),
         [B('button', 'Logs'), B('button', 'Scale up'), B('button', 'Terminate')]),
    ]
    for i, (q, p, ns) in enumerate(c7):
        rows.append(mk(q, p, ns, 'biz', f'c7-{i}', '业务动词映射'))
    c8 = [  # 航班/出行表单——目的地→To/Arrival 字段（t06/m13 同型）
        ('book a flight to Osaka', B('textbox', 'Arrival city'),
         [B('textbox', 'Departure city'), B('textbox', 'Depart date'),
          B('button', 'Find flights')]),
        ('查去曼谷的机票', B('textbox', '到达城市'),
         [B('textbox', '出发城市'), B('textbox', '出发日期'), B('button', '搜机票')]),
        ('search trains bound for Kyoto', B('textbox', 'Destination'),
         [B('textbox', 'Origin'), B('textbox', 'Travel date'), B('button', 'Find trains')]),
        ('想飞首尔，查票', B('textbox', '飞抵'),
         [B('textbox', '起飞地'), B('textbox', '日期'), B('button', '查询')]),
        ('flights heading to Berlin', B('textbox', 'To city'),
         [B('textbox', 'From city'), B('textbox', 'Return'), B('button', 'Fly search')]),
        ('买张去成都的动车票', B('textbox', '终到站'),
         [B('textbox', '始发站'), B('textbox', '乘车日期'), B('button', '查余票')]),
        ('get me a ride to the airport', B('textbox', 'Drop-off'),
         [B('textbox', 'Pick-up'), B('textbox', 'Time'), B('button', 'Request')]),
        ('ship this package to Madrid', B('textbox', 'Deliver to'),
         [B('textbox', 'Ship from'), B('textbox', 'Weight'), B('button', 'Quote')]),
    ]
    for i, (q, p, ns) in enumerate(c8):
        rows.append(mk(q, p, ns, 'biz', f'c8-{i}', '目的地→到达字段映射'))
    return rows


# ── D. 否定/排除语义（n22/m29/m30 同型）──
def bucket_d():
    rows = []
    d1 = [
        ('别点"稍后"，点"立即更新"', B('button', '立即更新'),
         [B('button', '稍后'), B('button', '忽略本次')]),
        ('不要"跳过"，要"继续安装"', B('button', '继续安装'),
         [B('button', '跳过'), B('button', '退出向导')]),
        ('别理"放弃"，按"同意并继续"', B('button', '同意并继续'),
         [B('button', '放弃'), B('button', '返回上步')]),
        ('skip the archive one, pin it instead', B('button', 'Pin'),
         [B('button', 'Archive item'), B('button', 'Snooze 10m')]),
        ('不点"删除"，点"移入收藏"', B('button', '移入收藏'),
         [B('button', '删除条目'), B('button', '分享')]),
        ('do NOT hit snooze, dismiss it', B('button', 'Dismiss'),
         [B('button', 'Snooze 10m'), B('button', '偏好设置')]),
        ('别选"导出"，选"同步"', B('button', '同步'),
         [B('button', '导出批次'), B('button', '备份')]),
        ('ignore the retry, force quit it', B('button', 'Force quit'),
         [B('button', 'Retry'), B('button', 'Wait')]),
    ]
    for i, (q, p, ns) in enumerate(d1):
        rows.append(mk(q, p, ns, 'neg', f'd1-{i}', '否定A取B'))
    d2 = [
        ('挑不是"跳过"的那一项', B('button', '继续设置'),
         [B('button', '跳过'), B('button', '退出')]),
        ('点不是"稍后"的那个按钮', B('button', '现在更新'),
         [B('button', '稍后'), B('button', '永不')]),
        ('click the option that is not "later"', B('button', 'Now'),
         [B('button', 'Later'), B('button', 'Never')]),
        ('选不是"放弃"的那个', B('button', '再试一次'),
         [B('button', '放弃'), B('button', '结束任务')]),
        ('pick anything but "decline"', B('button', 'Accept'),
         [B('button', 'Decline'), B('button', 'Postpone')]),
    ]
    for i, (q, p, ns) in enumerate(d2):
        rows.append(mk(q, p, ns, 'neg', f'd2-{i}', '排除指定项'))
    d3 = [
        ('除了"放弃"以外点哪个都行', B('button', '同意条款'),
         [B('button', '放弃'), B('button', '再想想')]),
        ('any button except "postpone"', B('button', 'Ship now'),
         [B('button', 'Postpone'), B('button', 'Review')]),
        ('除了"退出"之外的确认键', B('button', '确认退出编辑'),
         [B('button', '退出'), B('button', '留在此页')]),
        ('hit whatever is not "remind me later"', B('button', 'Enable now'),
         [B('button', 'Remind me later'), B('button', 'No thanks')]),
    ]
    for i, (q, p, ns) in enumerate(d3):
        rows.append(mk(q, p, ns, 'neg', f'd3-{i}', '除了X以外'))
    d4 = [  # 反向——防"goal 里点名的=负例"短路
        ('点"提交"，别管"存草稿"', B('button', '提交'),
         [B('button', '存草稿'), B('button', '预览')]),
        ('hit retry, not abort', B('button', 'Retry'),
         [B('button', 'Abort'), B('button', 'Inspect')]),
        ('按"保存"而不是"另存"', B('button', '保存'),
         [B('button', '另存'), B('button', '导出副本')]),
    ]
    for i, (q, p, ns) in enumerate(d4):
        rows.append(mk(q, p, ns, 'neg', f'd4-{i}', '正向点名防捷径'))
    return rows


# ── E. select/combobox 控件定位（m26/x18/x16-x23 同型）──
def bucket_e():
    rows = []
    e1 = [
        ('付款币种换成欧元', B('combobox', '结算币种 [CNY|EUR|GBP]'),
         [B('radio', '人民币现钞'), B('radio', '港币电汇'), B('button', '保存')]),
        ('switch theme to solarized', B('combobox', 'Theme [Light|Solarized|Auto]'),
         [B('radio', 'Light mode'), B('button', 'Preview'), B('link', 'Defaults')]),
        ('按价格高低排商品', B('combobox', '排序 [默认|价格|销量|上新]'),
         [B('button', '高级筛选'), B('radio', '只看有货'), B('link', '重置')]),
        ('把时区切成伦敦', B('combobox', 'Timezone [UTC|Europe/London|Asia/Tokyo]'),
         [B('radio', 'UTC'), B('button', '重置默认'), B('button', '应用')]),
        ('pick the EU server region', B('combobox', 'Region [US-East|EU-West|AP-South]'),
         [B('radio', 'EU'), B('button', 'Deploy'), B('button', 'Cancel')]),
        ('界面语言换成韩语', B('combobox', 'Language [中文|English|한국어]'),
         [B('button', '保存偏好'), B('link', '恢复默认'), B('radio', '跟随系统')]),
        ('deliver by drone please', B('combobox', 'Delivery [Van|Drone|Pickup]'),
         [B('radio', 'Van delivery'), B('radio', 'Pickup'), B('button', 'Checkout')]),
        ('字号调到特大', B('combobox', 'Font size [中|大|特大]'),
         [B('button', 'A- 缩小'), B('button', 'A+ 放大'), B('link', '默认排版')]),
        ('export it as an archive bundle', B('combobox', 'Export format [Report|Bundle|Raw]'),
         [B('button', '下载'), B('radio', 'Raw data'), B('link', '历史导出')]),
        ('账单币种选英镑', B('combobox', 'Billing currency [USD|GBP|CNY]'),
         [B('radio', '美元'), B('radio', '人民币账户'), B('button', '更新账单')]),
        ('pick shipping speed: courier', B('combobox', 'Speed [Economy|Courier|Same-day]'),
         [B('radio', 'Economy'), B('radio', 'Same-day'), B('button', 'Next')]),
        ('把默认仓库改成华南仓', B('combobox', '默认仓库 [华东仓|华南仓|西南仓]'),
         [B('radio', '华东仓'), B('radio', '西南仓'), B('button', '保存设置')]),
    ]
    for i, (q, p, ns) in enumerate(e1):
        rows.append(mk(q, p, ns, 'sel', f'e1-{i}', 'combobox opt_values 富化'))
    e2 = [
        ('从下拉里挑个尺码', B('combobox', 'Size [S|M|L]'),
         [B('radio', 'S 码'), B('radio', 'M 码'), B('button', '加入购物车')]),
        ('用下拉改默认打印机', B('combobox', 'Printer [Office-A|Office-B|PDF]'),
         [B('radio', 'Office-A'), B('radio', 'PDF 导出'), B('button', '打印')]),
        ('change country via the dropdown', B('combobox', 'Country [US|DE|JP]'),
         [B('radio', 'US only'), B('checkbox', 'Ship abroad'), B('button', 'Save')]),
        ('挑配送时段下拉里的晚间', B('combobox', '时段 [上午|下午|晚间]'),
         [B('radio', '上午送'), B('radio', '下午送'), B('button', '选定')]),
        ('select priority through the listbox', B('listbox', 'Priority [Low|Normal|Urgent]'),
         [B('radio', 'Low'), B('button', 'Apply'), B('link', '重置优先级')]),
        ('下拉选个铃声', B('combobox', 'Ringtone [默认|清脆|静音]'),
         [B('radio', '默认'), B('button', '试听'), B('button', '保存')]),
        ('choose the yearly billing cycle', B('combobox', 'Billing cycle [Monthly|Yearly]'),
         [B('radio', 'Monthly'), B('button', 'Upgrade'), B('link', 'Compare')]),
        ('通过下拉把精度改成高', B('combobox', '精度 [低|中|高]'),
         [B('radio', '低精度'), B('button', '预览效果'), B('link', '恢复')]),
        ('字号放大一档看着舒服点', B('button', 'A+ 放大'),
         [B('button', 'A- 缩小'), B('combobox', '字号 [小|中|大]'), B('button', '主题')]),
        ('make the text bigger', B('button', 'A↑ zoom in'),
         [B('button', 'A↓ zoom out'), B('combobox', 'Font [S|M|L]'), B('button', '换肤')]),
        ('把阅读器字体调大一点', B('button', '字体调大'),
         [B('button', '字体调小'), B('combobox', '行距 [1.0|1.5|2.0]'),
          B('button', '夜间模式')]),
    ]
    for i, (q, p, ns) in enumerate(e2):
        rows.append(mk(q, p, ns, 'sel', f'e2-{i}', '控件类型区分 combobox vs radio'))
    return rows


def main():
    rows = bucket_a() + bucket_b() + bucket_c() + bucket_d() + bucket_e()

    # ── 隔离区：同形态但不可标/判例性不确定组 ──
    quar('关掉第一条弹出的通知', 'ident-x',
         'unlearnable: 全部候选同名 button ×，正/负例文本一致标签矛盾')
    quar('下拉列表里挑第 2 项', 'sel-ordinal',
         'eval-semantic-twin: m26 选下拉的第二个选项；且 _meta_decide '
         '序数短路已接管，数据层不可修')
    quar('download the report as a spreadsheet', 'dl-twin',
         'eval-semantic-twin: n08 download the CSV report；meta "load" '
         '子串误命中 wait，数据层不可修')
    quar('expand all replies under this comment', 'replies-twin',
         'eval-semantic-twin: c10 展开回复线程；meta 滚动词误命中，不可修')
    quar('reply in this forum thread', 'reply-twin',
         'eval-semantic-twin: m12；且 expect=click 而 textbox→type，'
         '标注口径与 _ROLE_ACTION 冲突，不可判')
    quar('把地址二那栏勾上', 'addr-twin',
         'eval-semantic-twin: n05/m11 地址二→第二个地址 entry；'
         'CE 无位置信号，正例名不定无法收敛')

    # ── 闸门 1：query 与 eval goal exact+子串 ──
    hits = [(r['query'], g) for r in rows
            if any(_hit(r['query'], {g}) for g in EVAL_GOALS)]
    assert not hits, f'query 泄漏 eval: {hits[:5]}'

    # ── 闸门 2：miss case 候选文本零复用（pos+neg+candidates 全查）──
    bad = [(r['query'], t) for r in rows
           for t in [r['positive'], *r['negatives']] if t in FORBIDDEN]
    assert not bad, f'miss 候选文本复用: {bad[:5]}'

    # ── 闸门 3：query 去重 + positive 非空 + pos∉negs ──
    qs = [r['query'] for r in rows]
    assert len(qs) == len(set(qs)), 'query 重复'
    for r in rows:
        assert r['positive'].strip() and r['positive'] not in r['negatives']
    # pos/neg 跨行交叉：仅审计打印——格式族/role 族设计内必然出现
    # （同候选对 A goal 为正对 B goal 为负=对比信号本体，query 已去重
    #   故无同 (q,c) 双标签矛盾）
    pos_set = {r['positive'] for r in rows}
    xover = sorted({t for r in rows for t in r['negatives'] if t in pos_set})
    print(f'info: pos↔neg 跨行交叉 {len(xover)} 串（格式族/role 族对比信号，非矛盾）')

    with open(OUT, 'w', encoding='utf-8') as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False) + '\n')
    with open(OUT_Q, 'w', encoding='utf-8') as fp:
        for r in QUAR:
            fp.write(json.dumps(r, ensure_ascii=False) + '\n')
    json.dump(rows, open(OUT_ARR, 'w', encoding='utf-8'), ensure_ascii=False)

    from collections import Counter
    cnt = Counter(r['teacher_note'].split('：')[0] for r in rows)
    bkt = Counter(r['page'].split('/')[3] for r in rows)
    print(f'hardneg rows: {len(rows)} → {OUT.name} (+{OUT_ARR.name})')
    print(f'  buckets: {dict(bkt)}')
    print(f'  notes: {len(cnt)} 类')
    print(f'quarantine: {len(QUAR)} → {OUT_Q.name}')
    print('leak gate: query×eval-goal 0 命中；miss 候选串 0 复用')


if __name__ == '__main__':
    main()
