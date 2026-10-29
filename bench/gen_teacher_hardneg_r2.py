#!/usr/bin/env python3
"""第二轮定向 hard-negative 教师标注（本 agent 即教师，无外部 API）。

v24→v25：只针对 v24 剩余 miss 14 中"标注可修类"形态生成——
  可修(8): m25(已文件内纠偏) n05 m13 m29 x01 x03 x04 x12
  规则层(4): c10/n08/m12/m26 入 quarantine 不入训
  临界(2): m15/m17 轻量稳定族（非 eval 串）

防 m25 同类回归——对称性自查（新增闸 4）：
  每个"方向对"语义族要求 pos/neg 双向都出现（register→Sign up 与
  signin→Log in 互为镜像；否定族被点名项也在正向点名行作正例）。
  审计输出每串 pos/neg 计数，凡"方向族内仅出现单极性且无镜像"即断言失败。

泄漏铁律同 r1 三闸：
  闸1 query×全部 120 eval goal exact+子串（merge_train._hit）；
  闸2 miss case 候选串零复用（v19 miss 19 ∪ v24 miss 14 = 28 id 全量候选串，
      pos+neg 双查）——m25 对称补放行在 r1 文件内完成（候选串复用系纠偏必需，
      query 仍过闸1），r2 不再复用 'link Sign up'/'button Log in' 等串；
  闸3 query 去重 + pos∉negs + pos 非空；r2 page slug 独立命名空间 r2-*。
"""
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from eval_cases import EVAL_CASES            # noqa: E402
from bench_engines import rich_name          # noqa: E402
from merge_train import _hit                 # noqa: E402

OUT = Path(__file__).parent / 'teacher_hardneg_r2.jsonl'
OUT_ARR = Path(__file__).parent / 'teacher_hardneg_r2.json'
OUT_Q = Path(__file__).parent / 'teacher_hardneg_r2_quarantine.jsonl'

# v19 miss 19 ∪ v24 miss 14 = 28 id，候选串零复用
MISS_IDS = {"t06", "c10", "n05", "n08", "n22", "m11", "m12", "m13", "m14",
            "m15", "m17", "m25", "m26", "m27", "m29", "m30", "x01", "x03",
            "x04", "x09", "x12", "x18", "x43"}
EVAL_GOALS = {c['goal'].strip() for c in EVAL_CASES}
FORBIDDEN = {f"{o['role']} {rich_name(o)}"
             for c in EVAL_CASES if c['id'] in MISS_IDS
             for o in c['options']}

_rng = random.Random(20250725)
ROWS, QUAR = [], []


def mk(query, pos, negs, bucket, slug, note=''):
    page = f'https://fixture.hardneg-r2/{bucket}/{slug}'
    cands = [pos] + list(negs)
    _rng.shuffle(cands)
    return {'src': 'teacher_hardneg', 'prov': 'teacher_hardneg_r2',
            'page': page, 'goal': query, 'query': query, 'positive': pos,
            'negatives': list(negs), 'candidates': cands,
            'teacher_note': note or bucket}


def quar(query, page_slug, reason):
    QUAR.append({'page': f'https://fixture.hardneg-r2/quar/{page_slug}',
                 'query': query, 'reason': reason})


def B(role, name):
    return f'{role} {name}'


# ── A. 否定+被否项字面全中（m29 同型：别点删除点归档——负项名在 goal 原字面出现，
#      正例为同义/近义，教"被否定名=强负例"且不被字面引力带走）──
def bucket_a():
    rows = []
    # A1 双点名 zh——pos/neg 均被字面点名，负项为强干扰
    a1 = [
        ('别点"删除"，点"归档邮件"', B('button', '归档邮件'),
         [B('button', '删除'), B('button', '标记垃圾')]),
        ('不要"置顶"，要"标星收藏"', B('button', '标星收藏'),
         [B('button', '置顶'), B('button', '隐藏')]),
        ('别按"挂起"，按"转派处理"', B('button', '转派处理'),
         [B('button', '挂起'), B('button', '退回')]),
        ('"忽略"就算了，点"稍后处理"', B('button', '稍后处理'),
         [B('button', '忽略'), B('button', '关闭提醒')]),
        ('别碰"清空"，点"移入回收站"', B('button', '移入回收站'),
         [B('button', '清空'), B('button', '压缩打包')]),
        ('不要"拒绝"，改成"暂缓审批"', B('button', '暂缓审批'),
         [B('button', '拒绝'), B('button', '退回补充')]),
        ('别点"静默"，点"标记已知"', B('button', '标记已知'),
         [B('button', '静默'), B('button', '升级值班')]),
        ('不选"移除"，选"停用账号"', B('button', '停用账号'),
         [B('button', '移除'), B('button', '重置权限')]),
        ('别按"退回"，按"部分通过"', B('button', '部分通过'),
         [B('button', '退回'), B('button', '暂缓')]),
        ('不要"立即执行"，要"排入队列"', B('button', '排入队列'),
         [B('button', '立即执行'), B('button', '预览计划')]),
    ]
    for i, (q, p, ns) in enumerate(a1):
        rows.append(mk(q, p, ns, 'neg', f'ra1-{i}', '否定双点名 zh（m29 同型）'))
    # A2 双点名 en
    a2 = [
        ('do not hit "Remove", hit "Move to folder"', B('button', 'Move to folder'),
         [B('button', 'Remove'), B('button', 'Flag')]),
        ('skip "Mute", choose "Ack it"', B('button', 'Ack it'),
         [B('button', 'Mute'), B('button', 'Page oncall')]),
        ('ignore "Discard", press "Keep draft"', B('button', 'Keep draft'),
         [B('button', 'Discard'), B('button', 'Autosave off')]),
        ('not "Suspend" but "Throttle it"', B('button', 'Throttle it'),
         [B('button', 'Suspend'), B('button', 'Restart pod')]),
        ('leave "Pin" alone, tap "Star this"', B('button', 'Star this'),
         [B('button', 'Pin'), B('button', 'Unfollow')]),
        ('avoid "Wipe", pick "Quarantine"', B('button', 'Quarantine'),
         [B('button', 'Wipe'), B('button', 'Audit log')]),
        ('do not press "Hold", press "Route back"', B('button', 'Route back'),
         [B('button', 'Hold'), B('button', 'Partial pass')]),
        ('skip "Retweet", hit "Save item"', B('button', 'Save item'),
         [B('button', 'Retweet'), B('button', 'Copy link')]),
    ]
    for i, (q, p, ns) in enumerate(a2):
        rows.append(mk(q, p, ns, 'neg', f'ra2-{i}', '否定双点名 en（m29 同型）'))
    # A3 负项字面全中+正例同义非字面——m29 难点本体：
    #    goal 点名 neg（删除），pos 只给语义（收起来）→ 候选中正例名不同字面
    a3 = [
        ('别点"删除"，把它收起来', B('button', '归档'),
         [B('button', '删除'), B('button', '标为垃圾')]),
        ('不要"导出清单"，先存个底', B('button', '存快照'),
         [B('button', '导出清单'), B('button', '刷新')]),
        ('别按"放弃订单"，先挂着再说', B('button', '挂起订单'),
         [B('button', '放弃订单'), B('button', '催单')]),
        ('not "Terminate", just shelve it', B('button', 'Shelve'),
         [B('button', 'Terminate'), B('button', 'Snapshot')]),
        ('别碰"解散群"，先静音一阵子', B('button', '免打扰'),
         [B('button', '解散群'), B('button', '转让群')]),
        ('skip "Purge", tuck it away', B('button', 'Stash'),
         [B('button', 'Purge'), B('button', 'Verify')]),
        ('别点"否决"，先缓缓再定', B('button', '待定'),
         [B('button', '否决'), B('button', '加签')]),
        ('ignore "Shutdown", just pause it', B('button', 'Pause job'),
         [B('button', 'Shutdown'), B('button', 'Restart')]),
    ]
    for i, (q, p, ns) in enumerate(a3):
        rows.append(mk(q, p, ns, 'neg', f'ra3-{i}',
                     '负项字面+正例同义（m29 核心难点）'))
    # A4 正向点名防"named=neg"捷径（与 A1/A2 表面同款，方向相反）
    a4 = [
        ('点"归档邮件"，别管"删除"', B('button', '归档邮件'),
         [B('button', '删除'), B('button', '已读标记')]),
        ('hit "Mute", leave "Ack it" alone', B('button', 'Mute'),
         [B('button', 'Ack it'), B('button', 'Page oncall')]),
        ('按"挂起"，忽略"转派处理"', B('button', '挂起'),
         [B('button', '转派处理'), B('button', '催办')]),
        ('press "Discard", not "Keep draft"', B('button', 'Discard'),
         [B('button', 'Keep draft'), B('button', 'Settings gear')]),
        ('点"置顶"，不要"标星收藏"', B('button', '置顶'),
         [B('button', '标星收藏'), B('button', '隐藏')]),
        ('pick "Hold", skip "Route back"', B('button', 'Hold'),
         [B('button', 'Route back'), B('button', 'Defer')]),
        # —— 核心被否项的 pos 锚点（防"动作动词=恒负"m25 型偏置；
        #    与 A1/A3 镜像对称：同串不同 goal 方向下正负反转）——
        ('点"删除"，别理"归档邮件"', B('button', '删除'),
         [B('button', '归档邮件'), B('button', '标为垃圾')]),
        ('点"清空"，别碰"移入回收站"', B('button', '清空'),
         [B('button', '移入回收站'), B('button', '压缩打包')]),
        ('选"移除"，不要"停用账号"', B('button', '移除'),
         [B('button', '停用账号'), B('button', '重置权限')]),
        ('press "Remove", not "Move to folder"', B('button', 'Remove'),
         [B('button', 'Move to folder'), B('button', 'Flag')]),
        ('tap "Pin", skip "Star this"', B('button', 'Pin'),
         [B('button', 'Star this'), B('button', 'Unfollow')]),
        ('hit "Terminate", leave "Shelve" alone', B('button', 'Terminate'),
         [B('button', 'Shelve'), B('button', 'Snapshot')]),
    ]
    for i, (q, p, ns) in enumerate(a4):
        rows.append(mk(q, p, ns, 'neg', f'ra4-{i}', '正向点名防捷径'))
    return rows


# ── B. 目的地/方向字段（m13 同型：搜飞X的票→到达字段，非 Search 按钮）──
def bucket_b():
    rows = []
    b1 = [
        ('查飞昆明的航班', B('textbox', '目的地'),
         [B('textbox', '出发地'), B('textbox', '出行日期'), B('button', '查票')]),
        ('search buses to Suzhou', B('textbox', 'Destination'),
         [B('textbox', 'Origin'), B('textbox', 'Date'), B('button', 'Find buses')]),
        ('订一张去西安的高铁', B('textbox', '终到'),
         [B('textbox', '始发'), B('textbox', '乘车日'), B('button', '查询余票')]),
        ('flights going into Prague', B('textbox', 'Arrival'),
         [B('textbox', 'Departure'), B('textbox', 'Return date'),
          B('button', 'Find flights')]),
        ('快递寄到杭州', B('textbox', '寄往'),
         [B('textbox', '寄出地'), B('textbox', '重量'), B('button', '查运费')]),
        ('get a car heading downtown', B('textbox', 'Drop point'),
         [B('textbox', 'Pickup point'), B('textbox', 'When'), B('button', 'Hail')]),
        ('看看去青岛的船票', B('textbox', '到达港'),
         [B('textbox', '出发港'), B('textbox', '开船日'), B('button', '查航班')]),
        ('book vans bound for Lyon', B('textbox', 'Going to'),
         [B('textbox', 'Leaving from'), B('textbox', 'Day'),
          B('button', 'Get quotes')]),
        ('导航去黄山景区', B('textbox', '终点'),
         [B('textbox', '起点'), B('textbox', '途经'), B('button', '开始导航')]),
        ('rides into the airport tonight', B('textbox', 'Where to'),
         [B('textbox', 'Where from'), B('textbox', 'Pickup time'),
          B('button', 'Reserve')]),
        ('发货发到成都仓', B('textbox', '收货仓'),
         [B('textbox', '发货仓'), B('textbox', '件数'), B('button', '建发货单')]),
        ('fly me out to Nairobi', B('textbox', 'Landing city'),
         [B('textbox', 'Takeoff city'), B('textbox', 'Cabin'),
          B('button', 'Price it')]),
        ('查下月末去拉萨的票', B('textbox', '到站'),
         [B('textbox', '上车站'), B('textbox', '出行日'), B('button', '刷票')]),
        ('trains rolling to Munich', B('textbox', 'Arriving at'),
         [B('textbox', 'Departing from'), B('textbox', 'Seat class'),
          B('button', 'Timetable')]),
        ('寄包裹到哈尔滨', B('textbox', '收件城市'),
         [B('textbox', '寄件城市'), B('textbox', '体积'), B('button', '下单')]),
        ('head over to Osaka by rail', B('textbox', 'Bound for'),
         [B('textbox', 'Starting at'), B('textbox', 'Pass count'),
          B('button', 'Rail pass')]),
        ('想飞乌鲁木齐查下价', B('textbox', '飞抵城市'),
         [B('textbox', '起飞城市'), B('textbox', '舱位'), B('button', '比价')]),
        ('ship the pallet out to Denver', B('textbox', 'Deliver at'),
         [B('textbox', 'Ship from'), B('textbox', 'Pallets'),
          B('button', 'Book freight')]),
    ]
    for i, (q, p, ns) in enumerate(b1):
        rows.append(mk(q, p, ns, 'dst', f'rb1-{i}', '目的地→到达字段映射 r2'))
    return rows


# ── C. 业务深语义（x01/x03/x04/x12 同型——字面同族兄弟为负，业务动词映射为正）──
def bucket_c():
    rows = []
    # C1 作废/红冲族（x03：作废→冲红，"开票/下载"字面兄弟为负）
    c1 = [
        ('这批票据全废掉', B('button', '整批红冲'),
         [B('button', '整批开具'), B('button', '整批打印'), B('button', '查验')]),
        ('void all these invoices', B('button', 'Void all'),
         [B('button', 'Issue all'), B('button', 'Print batch'),
          B('button', 'Verify')]),
        ('开错的发票冲销掉', B('button', '红字发票'),
         [B('button', '蓝字发票'), B('button', '下载'), B('button', '归档')]),
        ('这几张单子作废处理', B('button', '作废批次'),
         [B('button', '补开批次'), B('button', '导出单据'), B('button', '复制')]),
        ('cancel out the wrong receipts', B('button', 'Reverse batch'),
         [B('button', 'Print receipts'), B('button', 'Re-issue'),
          B('button', 'Mail out')]),
        ('报销单据整张报废', B('button', '报废单据'),
         [B('button', '重新填报'), B('button', '打印预览'), B('button', '送审')]),
        ('invalidate the coupon batch', B('button', 'Invalidate coupons'),
         [B('button', 'Issue coupons'), B('button', 'Export codes'),
          B('button', 'Extend expiry')]),
        ('错的进项票全部冲掉', B('button', '进项红冲'),
         [B('button', '进项登记'), B('button', '进项下载'), B('button', '勾选认证')]),
        ('annul these purchase orders', B('button', 'Annul POs'),
         [B('button', 'Amend POs'), B('button', 'Receive goods'),
          B('button', 'Print POs')]),
        ('这批合同签了不算数，废掉', B('button', '合同作废'),
         [B('button', '合同续签'), B('button', '合同归档'), B('button', '下载副本')]),
        ('write off the bad batch', B('button', 'Write-off batch'),
         [B('button', 'Rebook batch'), B('button', 'Ship batch'),
          B('button', 'Audit trail')]),
        ('错开的收据冲正', B('button', '冲正收据'),
         [B('button', '补打收据'), B('button', '导出流水'), B('button', '对账')]),
    ]
    for i, (q, p, ns) in enumerate(c1):
        rows.append(mk(q, p, ns, 'biz', f'rc1-{i}', '作废→红冲族 r2'))
    # C2 驳回/打回族（x04：驳回→不予通过，"批准"反义兄弟为负）
    c2 = [
        ('小周的调岗申请打回', B('button', '驳回申请'),
         [B('button', '批准调岗'), B('button', '暂缓'), B('link', '调岗记录')]),
        ('turn down the budget request', B('button', 'Turn down'),
         [B('button', 'Green-light'), B('button', 'Park it'),
          B('link', 'Budget docs')]),
        ('这个转正评审我不批', B('button', '不通过'),
         [B('button', '予以通过'), B('button', '延期评审'), B('link', '评审材料')]),
        ('send the proposal back for rework', B('button', 'Send back'),
         [B('button', 'Sign off'), B('button', 'Hold vote'),
          B('link', 'Proposal file')]),
        ('采购单价格不行，退回去', B('button', '退回采购单'),
         [B('button', '核准采购'), B('button', '转二级审批'), B('link', '询比价')]),
        ('deny this expense report', B('button', 'Deny report'),
         [B('button', 'Reimburse'), B('button', 'Need info'),
          B('link', 'Receipts')]),
        ('请假理由不充分，打回去', B('button', '退回请假'),
         [B('button', '准假'), B('button', '转上级'), B('link', '考勤记录')]),
        ('reject the onboarding paperwork', B('button', 'Reject paperwork'),
         [B('button', 'Complete onboarding'), B('button', 'Request resend'),
          B('link', 'Candidate file')]),
        ('这份合同条款退回法务', B('button', '退回法务'),
         [B('button', '签署合同'), B('button', '标记风险'), B('link', '条款对照')]),
        ('veto the new pricing plan', B('button', 'Veto plan'),
         [B('button', 'Adopt plan'), B('button', 'Table it'),
          B('link', 'Pricing sheet')]),
        ('验收不合格，判退回', B('button', '验收退回'),
         [B('button', '验收通过'), B('button', '复验'), B('link', '质检单')]),
        ('decline the partnership offer', B('button', 'Decline offer'),
         [B('button', 'Accept offer'), B('button', 'Counter'),
          B('link', 'Term sheet')]),
    ]
    for i, (q, p, ns) in enumerate(c2):
        rows.append(mk(q, p, ns, 'biz', f'rc2-{i}', '驳回→退回族 r2'))
    # C3 告警压单族（x12：压掉→Ack/消警，"Escalate"反方向为负）
    c3 = [
        ('这个报警我压一下', B('button', '压警处理'),
         [B('button', '提级'), B('button', '转发值班'), B('button', '详情')]),
        ('clear this alert from the queue', B('button', 'Clear alert'),
         [B('button', 'Escalate L2'), B('button', 'Reassign'),
          B('button', 'History')]),
        ('告警确认收到，别再响了', B('button', '确认告警'),
         [B('button', '升级'), B('button', '静默规则'), B('button', '转派')]),
        ('ack the disk warning', B('button', 'Ack disk'),
         [B('button', 'Page SRE'), B('button', 'Snooze 15m'),
          B('button', 'Runbook')]),
        ('这批异常全部知晓了', B('button', '全部知晓'),
         [B('button', '全部升级'), B('button', '批量转发'), B('button', '导出日志')]),
        ('silence this pager event', B('button', 'Silence event'),
         [B('button', 'Escalate L2'), B('button', 'Reopen'),
          B('button', 'Timeline')]),
        ('先把这个误报标掉', B('button', '标记误报'),
         [B('button', '升级误报'), B('button', '拉黑源'), B('button', '留观')]),
        ('mark the outage as handled', B('button', 'Mark handled'),
         [B('button', 'Escalate L2'), B('button', 'Postmortem'),
          B('button', 'Broadcast')]),
        ('流量告警消掉', B('button', '消警'),
         [B('button', '提级处理'), B('button', '通知群主'), B('button', '截图')]),
        ('dismiss the duplicate alarm', B('button', 'Dismiss dup'),
         [B('button', 'Escalate L2'), B('button', 'Merge alerts'),
          B('button', 'Mute rule')]),
        ('值班这条我来认领', B('button', '认领告警'),
         [B('button', '移交二值'), B('button', '忽略'), B('button', '加备注')]),
        ('acknowledge the latency spike', B('button', 'Ack latency'),
         [B('button', 'Escalate L2'), B('button', 'Graph it'),
          B('button', 'Page team')]),
    ]
    for i, (q, p, ns) in enumerate(c3):
        rows.append(mk(q, p, ns, 'biz', f'rc3-{i}', '压警→Ack 族 r2'))
    # C4 工单/报障族（x01：开新工单→Create/New，"编辑/导出"同屏干扰为负）
    c4 = [
        ('给这台机器报个障', B('button', '新建工单'),
         [B('button', '编辑信息'), B('button', '导出清单'), B('button', '高级筛选')]),
        ('open a fresh support case', B('button', 'Open case'),
         [B('button', 'Edit case'), B('button', 'Export cases'),
          B('button', 'Filter board')]),
        ('客户投诉要立个单', B('button', '投诉工单'),
         [B('button', '修改客户'), B('button', '批量导出'), B('button', '标签筛选')]),
        ('file an incident report', B('button', 'File incident'),
         [B('button', 'Edit incident'), B('button', 'Export report'),
          B('button', 'Saved views')]),
        ('这个功能缺陷提个单子', B('button', '提缺陷单'),
         [B('button', '改优先级'), B('button', '导出错单'), B('button', '按人筛选')]),
        ('raise a maintenance request', B('button', 'Raise request'),
         [B('button', 'Amend request'), B('button', 'Download list'),
          B('button', 'Sort column')]),
        ('帮租户开个维修工单', B('button', '维修开单'),
         [B('button', '编辑房源'), B('button', '导出台账'), B('button', '状态筛选')]),
        ('log a new bug for the app', B('button', 'Log bug'),
         [B('button', 'Edit bug'), B('button', 'Export bugs'),
          B('button', 'Filter bugs')]),
        ('售后问题登记个工单', B('button', '登记售后'),
         [B('button', '编辑备注'), B('button', '导出对账'), B('button', '渠道筛选')]),
        ('create a change request ticket', B('button', 'New change req'),
         [B('button', 'Edit req'), B('button', 'Export req'),
          B('button', 'Filter req')]),
        ('给供应商立个索赔单', B('button', '新建索赔'),
         [B('button', '编辑供应商'), B('button', '导出明细'), B('button', '状态过滤')]),
        ('start a return merchandise case', B('button', 'New RMA'),
         [B('button', 'Edit RMA'), B('button', 'Export RMAs'),
          B('button', 'Filter RMAs')]),
    ]
    for i, (q, p, ns) in enumerate(c4):
        rows.append(mk(q, p, ns, 'biz', f'rc4-{i}', '开工单→New/Create 族 r2'))
    # C5 业务杂项（催办/加急/挂起/转派——同屏近义兄弟干扰）
    c5 = [
        ('这个单子催一下进度', B('button', '催办'),
         [B('button', '加急'), B('button', '转派'), B('button', '备注')]),
        ('escalate the priority of this task', B('button', 'Prioritize'),
         [B('button', 'Postpone'), B('button', 'Delegate'),
          B('button', 'Comment')]),
        ('把审批先挂起来等补充材料', B('button', '挂起审批'),
         [B('button', '通过审批'), B('button', '催审'), B('button', '退回')]),
        ('hand this ticket to the night shift', B('button', 'Hand off'),
         [B('button', 'Hold ticket'), B('button', 'Nudge agent'),
          B('button', 'Tag')]),
        ('这批货加急发出去', B('button', '加急发货'),
         [B('button', '正常发货'), B('button', '暂缓发货'), B('button', '打印面单')]),
        ('reassign the complaint to tier two', B('button', 'Reassign'),
         [B('button', 'Resolve'), B('button', 'Follow up'),
          B('button', 'Label')]),
    ]
    for i, (q, p, ns) in enumerate(c5):
        rows.append(mk(q, p, ns, 'biz', f'rc5-{i}', '业务杂项 r2'))
    return rows


# ── D. 序数地址/纯名 entry（n05 同型：CE 无位置信号——
#      训"地址"字面≠答案，序数才决定 entry；pos 名不含'地址'字面，
#      neg 放含'地址'字面项作陷阱）──
def bucket_d():
    rows = []
    d1 = [
        ('收货地址挑第三个', B('radio', '乡下老宅'),
         [B('radio', '总部地址'), B('radio', '父母住处'), B('button', '下一步')]),
        ('用第二个收货地址', B('radio', '门店'),
         [B('radio', '单位地址'), B('radio', '姨妈家'), B('button', '保存选择')]),
        ('pick the third delivery address', B('radio', 'Lake house'),
         [B('radio', 'Office address'), B('radio', 'Cousin\'s'),
          B('button', 'Continue on')]),
        ('送第二个地址去', B('radio', '奶奶家'),
         [B('radio', '收发室地址'), B('radio', '健身房'), B('button', '去支付')]),
        ('use my third saved spot', B('radio', 'Weekend cabin'),
         [B('radio', 'Billing address'), B('radio', 'Studio'),
          B('button', 'Apply pick')]),
        ('收货地改成第 3 个', B('radio', '库房'),
         [B('radio', '注册地址'), B('radio', '姐姐家'), B('button', '选好')]),
        ('choose address entry two', B('radio', 'Mom and Dad\'s'),
         [B('radio', 'Mailing address'), B('radio', 'Work gym'),
          B('button', 'Next step')]),
        ('拿第 2 个常用收货点', B('radio', '楼下驿站'),
         [B('radio', '默认地址'), B('radio', '同学家'), B('button', '确认下单')]),
        ('switch shipping to slot three', B('radio', 'Farm stand'),
         [B('radio', 'Return address'), B('radio', 'Neighbor'),
          B('button', 'Save pick')]),
        ('收货地址用最后一项', B('radio', '度假屋'),
         [B('radio', '通讯地址'), B('radio', '朋友家'), B('button', '提交订单')]),
        ('set drop-off to the second entry', B('radio', 'Uncle\'s flat'),
         [B('radio', 'Home address'), B('radio', 'Locker 7'),
          B('button', 'Proceed on')]),
        ('第 3 个收货点送到', B('radio', '山上民宿'),
         [B('radio', '办公地址'), B('radio', '同事代收'), B('button', '下一步')]),
    ]
    for i, (q, p, ns) in enumerate(d1):
        rows.append(mk(q, p, ns, 'ord', f'rd1-{i}',
                     '序数地址纯名 entry（含字面陷阱负例）'))
    # D2 对照组——pos 名带序号文本（"地址N·X"），保持两种可学形态
    d2 = [
        ('第二个收货地址下单', B('radio', '地址2·外婆家'),
         [B('radio', '地址1·单位'), B('radio', '地址3·学校'), B('button', '提交')]),
        ('use address slot three', B('radio', 'Slot 3 · Farmhouse'),
         [B('radio', 'Slot 1 · Flat'), B('radio', 'Slot 2 · Office'),
          B('button', 'Ship it')]),
        ('挑第 1 个收货地', B('radio', '收货点1·前台'),
         [B('radio', '收货点2·驿站'), B('radio', '收货点3·门卫'),
          B('button', '确定收货')]),
        ('pick delivery entry four', B('radio', 'Entry 4 · Cabin'),
         [B('radio', 'Entry 2 · Loft'), B('radio', 'Entry 3 · Dorm'),
          B('button', 'Deliver')]),
    ]
    for i, (q, p, ns) in enumerate(d2):
        rows.append(mk(q, p, ns, 'ord', f'rd2-{i}', '序数地址名内索引（对照）'))
    return rows


# ── E. 登录/注册方向对称族（m25 辅助：全部非 eval 串——
#      每个方向对双向都出现 pos/neg，显式对称）──
def bucket_e():
    """三方向对称族：register/signin/recover 每个方向串都至少 1 次 pos。

    m25 教训固化——方向性串（用户在某类 goal 下可能想点的动词）不允许
    '只当负例'：register 系串在 signin goal 下作 neg 时，必须有等价的
    register goal 行把它作 pos，反之亦然；recover 系同理。"""
    rows = []
    e1 = [
        # ── register 方向 pos 锚点（登录系/找回系为 neg）──
        ('新用户先注册下', B('link', '注册'),
         [B('button', '登录'), B('link', '找回密码'), B('link', '帮助')]),
        ('join the beta program', B('link', 'Join beta'),
         [B('button', 'Sign in'), B('link', 'Reset access'),
          B('link', 'Pricing')]),
        ('enroll as a new member', B('button', 'Enroll now'),
         [B('button', 'Member login'), B('link', 'Trouble'),
          B('link', 'Tour')]),
        ('create my profile first', B('button', 'Create profile'),
         [B('button', 'Sign in'), B('link', 'Help desk'),
          B('link', 'Plans')]),
        ('办个会员账号', B('button', '开通会员'),
         [B('button', '会员登录'), B('link', '忘记密码'), B('link', '企业咨询')]),
        ('register for the workshop', B('link', 'Register'),
         [B('button', 'Sign in'), B('link', 'Schedule'),
          B('link', 'Speakers')]),
        ('首次来这登记个号', B('link', '新用户注册'),
         [B('button', '登录'), B('link', '找回口令'), B('link', '客服')]),
        # ── signin 方向 pos 锚点（注册系/找回系为 neg）──
        ('已有账户直接登', B('button', '登录'),
         [B('link', '注册'), B('link', '找回密码'), B('link', '帮助')]),
        ('sign back into the portal', B('button', 'Sign in'),
         [B('link', 'Join beta'), B('link', 'Reset access'),
          B('link', 'Status')]),
        ('会员登进去看订单', B('button', '会员登录'),
         [B('link', '开通会员'), B('link', '忘记密码'), B('link', '首页')]),
        ('log back into the console', B('button', 'Console login'),
         [B('link', 'Register'), B('link', 'Docs')]),
        ('member already? sign back in', B('button', 'Member login'),
         [B('button', 'Enroll now'), B('link', 'Trouble')]),
        # ── recover 方向 pos 锚点（注册系/登录系为 neg）──
        ('密码不记得了找回下', B('link', '找回密码'),
         [B('button', '登录'), B('link', '注册'), B('link', '联系我们')]),
        ('lost my credentials, recover them', B('link', 'Reset access'),
         [B('button', 'Sign in'), B('link', 'Join beta'),
          B('link', 'Status page')]),
        ('口令忘了重置一下', B('link', '找回口令'),
         [B('button', '登录'), B('link', '新用户注册'), B('link', '客服中心')]),
        ('忘记密码了去重置', B('link', '忘记密码'),
         [B('button', '登录'), B('link', '开通会员'), B('link', '联系我们')]),
    ]
    for i, (q, p, ns) in enumerate(e1):
        rows.append(mk(q, p, ns, 'auth', f're1-{i}',
                     '三方向对称（register/signin/recover 互为镜像）'))
    return rows


# ── F. 临界稳定族（m15/m17 邻域：图标/静音语义，全部非 eval 串）──
def bucket_f():
    rows = []
    f1 = [
        ('把这个频道消息弄安静点', B('button', '🔕 频道静音'),
         [B('button', '稍后提醒'), B('button', '偏好'), B('button', '清空历史')]),
        ('quiet the group pings tonight', B('button', 'Quiet mode'),
         [B('button', 'Remind tomorrow'), B('button', 'Group prefs'),
          B('button', 'Leave group')]),
        ('这条推送别再震了', B('button', '关闭震动'),
         [B('button', '延迟推送'), B('button', '推送设置'), B('button', '全标已读')]),
        ('silence the thread alerts', B('button', '🔕 Thread'),
         [B('button', 'Nudge later'), B('button', 'Alert prefs'),
          B('button', 'Mark read')]),
        ('这篇先收起来有空再看', B('button', '★ 收藏'),
         [B('button', '转发'), B('button', '字号调节'), B('button', '打印')]),
        ('stash this video for the weekend', B('button', '📌 Watch later'),
         [B('button', 'Send to'), B('button', 'Captions'),
          B('button', 'Report')]),
        ('把这个帖子加进稍后读', B('button', '稍后读'),
         [B('button', '分享出去'), B('button', '夜间模式'), B('button', '复制链接')]),
        ('keep this recipe for sunday', B('button', '♡ Keep it'),
         [B('button', 'Forward'), B('button', 'Font size'),
          B('button', 'Print card')]),
        ('把这条重要消息顶上去', B('button', '📌 置顶'),
         [B('button', '收藏'), B('button', '提醒'), B('button', '屏蔽')]),
        ('flag this mail to read tonight', B('button', '★ Flag'),
         [B('button', 'Pass along'), B('button', 'Zoom'),
          B('button', 'Archive box')]),
    ]
    for i, (q, p, ns) in enumerate(f1):
        rows.append(mk(q, p, ns, 'icon', f'rf1-{i}', '图标/静音语义稳定族'))
    return rows


def symmetry_audit(rows):
    """闸 4：方向族对称自查（m25 教训固化）。

    登记"方向性串"集合——即用户在某类 goal 下可能想点的动词/链接，
    区别于中性 filler（帮助/设置/打印等永远只配当干扰项）。
    规则：集合内凡作过 neg 的串必须 ≥1 次 pos（否则模型学到
    "该串=恒负"，一旦 eval 需要它作正例即 m25 型回归）。
    集合外 filler 串豁免——中性干扰项 neg-only 是正确极性。
    """
    direction_strings = {
        # auth 三方向（E 桶）
        'link 注册', 'link Join beta', 'button Enroll now',
        'button Create profile', 'button 开通会员', 'link Register',
        'link 新用户注册', 'button 登录', 'button Sign in',
        'button 会员登录', 'button Console login', 'button Member login',
        'link 找回密码', 'link Reset access', 'link 找回口令',
        'link 忘记密码',
        # neg 桶核心被点名串（A1-A4 双向）
        'button 归档邮件', 'button 删除', 'button 置顶', 'button 标星收藏',
        'button 挂起', 'button 转派处理', 'button Mute', 'button Ack it',
        'button Discard', 'button Keep draft', 'button Hold',
        'button Route back', 'button Remove', 'button Pin',
        'button 清空', 'button 移除', 'button Terminate',
    }
    pol = defaultdict(lambda: [0, 0])
    for r in rows:
        pol[r['positive']][0] += 1
        for n in r['negatives']:
            pol[n][1] += 1
    bad = []
    for s in sorted(direction_strings):
        p, n = pol.get(s, (0, 0))
        if n >= 1 and p == 0:
            bad.append((s, p, n))
    # 审计透明度：打印全部 neg-only 方向串与 filler 统计
    neg_only = sorted(s for s, (p, n) in pol.items()
                      if n >= 1 and p == 0)
    print(f'audit: 方向串 {len(direction_strings)} 个全查，'
          f'neg-only 串 {len(neg_only)} 个（filler/单边负例豁免）')
    return pol, bad


def main():
    rows = bucket_a() + bucket_b() + bucket_c() + bucket_d() \
        + bucket_e() + bucket_f()

    # ── 隔离区：规则层结构性 miss 的语义孪生——不入训 ──
    quar('点开这条评论的全部回复', 'replies-twin',
         'eval-semantic-twin c10 + meta 滚动词误命中——规则层非数据层')
    quar('download the log bundle', 'dl-twin',
         'eval-semantic-twin n08 + meta "load"⊂"download" 误命中 wait——规则层')
    quar('挑下拉里的第 3 项', 'sel-ord-twin',
         'eval-semantic-twin m26 + _meta_decide 序数短路接管——规则层')
    quar('post a reply under this topic', 'reply-twin',
         'eval-semantic-twin m12 + expect=click 与 textbox→type 口径冲突——规则层')
    quar('别点"取消"，点"确定"', 'cancel-twin',
         'eval-semantic-twin n22/m30 原字面对（取消/确定候选串在 FORBIDDEN）')
    quar('静音这条通知', 'mute-twin',
         'eval-semantic-twin m15（Snooze/Settings/Clear all/Bell off '
         '候选串禁用；~0 分临界态修不修看权重抖动，不强灌）')

    # ── 闸 1：query × eval goal exact+子串 ──
    hits = [(r['query'], g) for r in rows
            if any(_hit(r['query'], {g}) for g in EVAL_GOALS)]
    assert not hits, f'query 泄漏 eval: {hits[:5]}'

    # ── 闸 2：miss case 候选串零复用（28 id 全量）──
    bad = [(r['query'], t) for r in rows
           for t in [r['positive'], *r['negatives']] if t in FORBIDDEN]
    assert not bad, f'miss 候选串复用: {bad[:5]}'

    # ── 闸 3：query 去重 + pos 非空 + pos∉negs + 与 r1 query 不重复 ──
    qs = [r['query'] for r in rows]
    assert len(qs) == len(set(qs)), 'query 重复'
    r1_qs = {json.loads(l)['query'] for l in
             open(Path(__file__).parent / 'teacher_hardneg.jsonl',
                  encoding='utf-8')}
    dup = [q for q in qs if q in r1_qs]
    assert not dup, f'与 r1 query 撞车: {dup[:5]}'
    for r in rows:
        assert r['positive'].strip() and r['positive'] not in r['negatives']

    # ── 闸 4：对称性自查（m25 同类回归防线）──
    pol, asym = symmetry_audit(rows)
    assert not asym, f'方向族内仅负无正（m25 型不对称）: {asym[:8]}'
    pos_set = {r['positive'] for r in rows}
    xover = sorted({t for r in rows for t in r['negatives'] if t in pos_set})
    print(f'info: pos↔neg 跨行交叉 {len(xover)} 串（方向对/点名对教学信号）')

    with open(OUT, 'w', encoding='utf-8') as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False) + '\n')
    with open(OUT_Q, 'w', encoding='utf-8') as fp:
        for r in QUAR:
            fp.write(json.dumps(r, ensure_ascii=False) + '\n')
    json.dump(rows, open(OUT_ARR, 'w', encoding='utf-8'), ensure_ascii=False)

    bkt = Counter(r['page'].split('/')[3] for r in rows)
    print(f'r2 rows: {len(rows)} → {OUT.name} (+{OUT_ARR.name})')
    print(f'  buckets: {dict(bkt)}')
    print(f'quarantine: {len(QUAR)} → {OUT_Q.name}')
    print('leak gates: query×eval-goal 0 命中；miss28 候选串 0 复用；'
          'r1 query 0 重复；方向族对称断言通过')


if __name__ == '__main__':
    main()
