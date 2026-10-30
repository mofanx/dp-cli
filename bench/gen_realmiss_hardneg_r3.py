#!/usr/bin/env python3
"""R3 真机残余 miss 定向教师标注（本 agent 即教师，无外部 API）。

数据源：/tmp/recall_attrib2_final2.json 归因残余 18 组
  A_top3(11)：目标在 CE 全排 rank2-3 但截断 top8 内 top1 错
  B_recall(7)：目标被 embedding top8 召回层裁掉
标注口径：goal+candidates 取自 ~/.dp_cli/hard_cases.jsonl，正例经
  /tmp/recall_attrib2.json pool + final2.T 字段交叉核对；
  negatives 取 top3 干扰 + 同族兄弟硬负例；每组扩 2-3 同形态变体
  （同判别难点不同表述，防撞 eval）。

三泄漏闸（沿 r1/r2/r26 口径）：
  闸1 query×全部 120 eval goal exact+子串（merge_train._hit）；
  闸2 miss-eval 候选串零复用（历史 miss id 并集全量候选串，pos+neg 双查）；
  闸3 query 去重（组内+对 merge_out_v24）+ pos∉negs + pos 非空。

quarantine：yc comments(T 判据已含 top1 命中=伪 miss)、
  bing videos 时长(T.syn 已含 top1 命中=标注口径矛盾)——不猜标。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from eval_cases import EVAL_CASES            # noqa: E402
from bench_engines import rich_name          # noqa: E402
from merge_train import _hit                 # noqa: E402

OUT = Path(__file__).parent / 'realmiss_hardneg.jsonl'
OUT_Q = Path(__file__).parent / 'realmiss_hardneg_quarantine.jsonl'
V24 = Path(__file__).parent / 'merge_out_v24.json'

# 历史 miss id 并集（r2 28 ∪ v26 新增 m30/x15/x50）——候选串零复用
MISS_IDS = {"t06", "c10", "n05", "n08", "n22", "m11", "m12", "m13", "m14",
            "m15", "m17", "m25", "m26", "m27", "m29", "m30", "x01", "x03",
            "x04", "x09", "x12", "x15", "x18", "x43", "x50"}
EVAL_GOALS = {c['goal'].strip() for c in EVAL_CASES}
FORBIDDEN = {f"{o['role']} {rich_name(o)}"
             for c in EVAL_CASES if c['id'] in MISS_IDS
             for o in c['options']}

V24_QUERIES = {r['query'].strip()
               for r in json.loads(V24.read_text(encoding='utf-8'))}

ROWS, QUAR, SEEN_Q = [], [], set()


def mk(query, page, pos, negs, slug, note):
    cands = [pos] + list(negs)
    return {'src': 'teacher_hardneg', 'prov': 'teacher_realmiss',
            'page': page, 'goal': query, 'query': query, 'positive': pos,
            'negatives': list(negs), 'candidates': cands,
            'teacher_note': f'{slug}: {note}'}


def add(query, page, pos, negs, slug, note):
    r = mk(query, page, pos, negs, slug, note)
    qs = query.strip()
    # 闸1 query×eval goal
    if _hit(qs, EVAL_GOALS):
        QUAR.append({'query': query, 'slug': slug,
                     'reason': 'gate1: query 命中 eval goal'})
        return
    # 闸2 miss 候选串零复用（pos+neg 双查）
    for s in [pos] + list(negs):
        if s in FORBIDDEN:
            QUAR.append({'query': query, 'slug': slug, 'str': s,
                         'reason': 'gate2: 复用 miss-eval 候选串'})
            return
    # 闸3 query 去重 + pos∉negs + pos 非空
    if qs in SEEN_Q or qs in V24_QUERIES:
        QUAR.append({'query': query, 'slug': slug,
                     'reason': 'gate3: query 重复（组内或 v24）'})
        return
    if not pos or pos in negs:
        QUAR.append({'query': query, 'slug': slug,
                     'reason': 'gate3: pos 空或 ∈negs'})
        return
    SEEN_Q.add(qs)
    ROWS.append(r)


def B(role, name):
    return f'{role} {name}'


LOGIN = 'file:///home/yan/ubuntu/project/github/dp-cli/bench/fixtures/login.html'
ZHIHU = 'https://www.zhihu.com/signin?next=%2F'
BILI = 'https://www.bilibili.com/'
BAIDU = 'https://www.baidu.com/'
OSCHINA = 'https://www.oschina.net/'
CSDN = 'https://so.csdn.net/so/search?q=python'
BING_S = ('https://cn.bing.com/search?q=%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD'
          '&rdr=1&rdrig=FCC38AF388FC4E3C9366CEFA006C09B0')
DB_GRP = 'https://www.douban.com/group/661193/'
DB_EXP = 'https://www.douban.com/group/explore'
K12306 = 'https://kyfw.12306.cn/otn/leftTicket/init'
DB_BOOK = 'https://book.douban.com/subject/2567698/'
WEATHER = 'https://www.weather.com.cn/weather/101010100.shtml'

# ── G1 login.html 登录按钮（A_top3：top1=Username textbox 赢过 Sign In）──
POS = B('button', 'Sign In')
add('点击登录按钮', LOGIN, POS,
    [B('textbox', 'Username'), B('textbox', 'Password'),
     B('checkbox', 'Remember me')], 'g1',
    'top3 全表单项赢过登录按钮——role/意图判别')
add('按下登录键提交表单', LOGIN, POS,
    [B('link', 'Forgot password?'), B('link', 'Create account'),
     B('textbox', 'Password')], 'g1',
    '同型 en 辅助链接为负')
add('submit the login form', LOGIN, POS,
    [B('textbox', 'Username'), B('link', 'Create account'),
     B('checkbox', 'Remember me')], 'g1', 'en 变体')

# ── G2 zhihu 登录按钮（A_top3×2：top1=密码登录 tab 赢过 登录/注册 submit）──
POS = B('button', '登录/注册')
add('点登录按钮', ZHIHU, POS,
    [B('button', '密码登录'), B('button', '验证码登录'),
     B('textbox', '手机号')], 'g2',
    'tab 名含"登录"字面引力赢 submit——本组 miss 本体')
add('点下登录那个按钮', ZHIHU, POS,
    [B('button', '密码登录'), B('button', '获取短信验证码'),
     B('link', '开通机构号')], 'g2', '原 query"点击登录按钮"变体'
    '（原串与 g1 login.html 同 query 跨页冲突，闸3 改名）')
add('确认登录进去', ZHIHU, POS,
    [B('button', '验证码登录'), B('textbox', '输入 6 位短信验证码'),
     B('link', '注册机构号 ·')], 'g2', '变体')
add('sign in to my account', ZHIHU, POS,
    [B('button', '密码登录'), B('button', '获取短信验证码'),
     B('link', '开通机构号')], 'g2', 'en 变体')

# ── G3 zhihu 国家区号（B_recall：combobox 中国 +86 被 top8 裁掉）──
POS = B('combobox', '中国 +86')
add('选择国家区号', ZHIHU, POS,
    [B('textbox', '手机号'), B('link', '开通机构号'),
     B('button', '获取短信验证码')], 'g3',
    'combobox 语义弱于字面——区号→combobox 映射')
add('把手机号区号换掉', ZHIHU, POS,
    [B('textbox', '手机号'), B('textbox', '输入 6 位短信验证码'),
     B('button', '获取短信验证码')], 'g3', '变体')
add('switch the phone country code', ZHIHU, POS,
    [B('textbox', '手机号'), B('button', '获取短信验证码'),
     B('link', '注册机构号 ·')], 'g3', 'en 变体')

# ── G4 bilibili 搜索框（B_recall：textbox(占位名)被裁）──
POS = B('textbox', '崩坏星穹铁道')
add('在搜索框输入内容', BILI, POS,
    [B('link', '知识'), B('clickable', '立即登录'),
     B('button', '换一换')], 'g4',
    '搜索框 a11y 名=占位热词无"搜索"字面——role 判别')
add('往顶部搜索栏打字', BILI, POS,
    [B('link', '首页'), B('link', '热门'), B('clickable', '立即登录')],
    'g4', '变体')
add('type a keyword in the search box', BILI, POS,
    [B('link', '知识'), B('button', '换一换'), B('link', '直播')],
    'g4', 'en 变体')

# ── G5 baidu 搜索框 ×2（B_recall：textbox(占位名)被裁，top1=百度一下）──
POS = B('textbox', '老外想去赏秋被司机拉到商丘')
add('在搜索框输入 Python 教程', BAIDU, POS,
    [B('button', '百度一下'), B('link', '百度热搜'),
     B('link', '使用百度前必读')], 'g5', '原 query——按钮名含"百度"引力')
add('在搜索框输入天气预报', BAIDU, POS,
    [B('button', '百度一下'), B('link', '百度热搜'),
     B('link', '百度搭子')], 'g5', '原 query 第二条')
add('在搜索栏里敲关键词', BAIDU, POS,
    [B('button', '百度一下'), B('link', 'hao123'),
     B('link', '更多')], 'g5', '变体')
add('enter a query in the search field', BAIDU, POS,
    [B('button', '百度一下'), B('link', '百度热搜'),
     B('link', '使用百度前必读')], 'g5', 'en 变体')

# ── G6 oschina 开源软件栏目（A_top3：top1=搜索 textbox 赢过 link 软件）──
POS = B('link', '软件')
add('点击开源软件栏目', OSCHINA, POS,
    [B('tab', '开源资讯'), B('tab', '软件资讯'),
     B('textbox', '搜索资讯、博客、软件……')], 'g6',
    '栏目导航 link vs 同族 tab/textbox——"软件"短名消歧')
add('进入开源软件频道', OSCHINA, POS,
    [B('tab', '开源资讯'), B('link', '开源软件推进联盟'),
     B('textbox', '搜索资讯、博客、软件……')], 'g6',
    '联盟页脚链字面全中为强负例')
add('打开软件板块', OSCHINA, POS,
    [B('tab', '软件资讯'), B('link', '动弹'), B('link', '专区')],
    'g6', '变体')

# ── G7 csdn 按时间排序（A_top3：clickable 最新 rank3 输 link 快速提问）──
POS = B('clickable', '最新')
add('点击按时间排序', CSDN, POS,
    [B('clickable', '综合'), B('clickable', '筛选'),
     B('link', '快速提问')], 'g7',
    '时间排序→"最新"映射，兄弟 tab 综合为负')
add('把结果按最新排列', CSDN, POS,
    [B('clickable', '综合'), B('clickable', '筛选'),
     B('link', '招贤纳士')], 'g7', '变体')
add('sort results by time', CSDN, POS,
    [B('clickable', '综合'), B('link', '立即登录'),
     B('clickable', 'VIP内容')], 'g7', 'en 变体')

# ── G8 bing 重新搜（A_top3：searchbox rank3 输 link 返回到必应搜索）──
POS = B('searchbox', '在此处输入你的搜索 — 输入时会显示搜索建议')
add('重新搜机器学习', BING_S, POS,
    [B('link', '返回到必应搜索'), B('button', '搜索'),
     B('link', '图片')], 'g8',
    '"重新搜X"→改写 searchbox 非点搜索钮/返回')
add('换个关键词重新检索', BING_S, POS,
    [B('button', '搜索'), B('link', '返回到必应搜索'),
     B('link', '人工智能（智能科学与技术专业术语）_百度百科')], 'g8', '变体')
add('把搜索词改成深度学习', BING_S, POS,
    [B('button', '搜索'), B('link', '图片'), B('link', '航班')],
    'g8', '变体')

# ── G9 douban 组内搜（A_top3：textbox 搜索本组讨论 rank2 输 link）──
POS = B('textbox', '搜索本组讨论')
add('在组内搜穿搭', DB_GRP, POS,
    [B('link', '豆瓣小组'), B('link', '小组'), B('button', '搜索')],
    'g9', '"组内搜"→本组 textbox 非导航 link')
add('搜索组里的穿搭帖子', DB_GRP, POS,
    [B('textbox', '小组、讨论'), B('link', '小组'),
     B('link', '豆瓣小组')], 'g9',
    '组内 box vs 全站 box——本组判别难点')
add('find posts about outfits in this group', DB_GRP, POS,
    [B('textbox', '小组、讨论'), B('button', '搜索'),
     B('link', '豆瓣小组')], 'g9', 'en 变体')

# ── G10 12306 只看高铁动车（A_top3：checkbox rank2 输 link 中国铁路12306）──
add('勾选只看高铁动车', K12306, B('checkbox', 'GC-高铁/城际'),
    [B('checkbox', 'Z-直达'), B('checkbox', 'T-特快'),
     B('combobox', '共5项,按上下键进行选择,按回车键确认')], 'g10',
    '高铁动车→GC/D checkbox，普速类型为硬负例')
add('只看高铁和动车', K12306, B('checkbox', 'GC-高铁/城际'),
    [B('checkbox', 'K-快速'), B('checkbox', '其他'),
     B('checkbox', '显示全部可预订车次')], 'g10', '变体')
add('勾上动车筛选', K12306, B('checkbox', 'D-动车'),
    [B('checkbox', 'Z-直达'), B('checkbox', 'K-快速'),
     B('link', '中国铁路12306')], 'g10', 'D-动车对称锚点')

# ── G11 book.douban 作者简介（A_top3：作者长链 rank3 输 全部原文摘录）──
POS = B('link', '作者: 刘慈欣 出版社: 重庆出版社 出品方: 科幻世界 '
        '出版年: 2008-1 ISBN: 9787536692930 页数: 302 装帧: 平装 定价: ')
add('点作者简介链接', DB_BOOK, POS,
    [B('link', '全部原文摘录'), B('link', '写书评'),
     B('link', '我要写书评')], 'g11',
    '作者信息块长链 vs 书评/摘录同屏干扰')
add('打开作者介绍页', DB_BOOK, POS,
    [B('link', '全部原文摘录'), B('link', '更多书评 4809篇'),
     B('link', '我要写书评')], 'g11', '变体')
add('open the author info link', DB_BOOK, POS,
    [B('link', '写书评'), B('link', '全部原文摘录'),
     B('link', '更多书评 4809篇')], 'g11', 'en 变体')

# ── G12 douban explore 搜租房小组（B_recall：textbox 小组、讨论被裁）──
POS = B('textbox', '小组、讨论')
add('搜索租房相关的小组', DB_EXP, POS,
    [B('link', '热门小组'), B('link', '讨论精选'),
     B('link', '豆瓣小组')], 'g12',
    '搜X的小组→textbox 非小组导航 link')
add('找一下租房的小组', DB_EXP, POS,
    [B('link', '热门小组'), B('link', '懒人生活指北小组'),
     B('link', '豆瓣小组')], 'g12', '变体')
add('look up groups about renting', DB_EXP, POS,
    [B('link', '讨论精选'), B('link', '热门小组'),
     B('link', '读书')], 'g12', 'en 变体')

# ── G13 bing 百度百科那条（B_recall：百科 link 被裁，top1=searchbox）──
POS = B('link', '人工智能（智能科学与技术专业术语）_百度百科')
add('点百度百科那条', BING_S, POS,
    [B('searchbox', '在此处输入你的搜索 — 输入时会显示搜索建议'),
     B('link', '返回到必应搜索'), B('button', '搜索')], 'g13',
    '结果条目 link vs 页头控件——"那条"指结果项')
add('点开百科词条', BING_S, POS,
    [B('searchbox', '在此处输入你的搜索 — 输入时会显示搜索建议'),
     B('button', '搜索'), B('link', '图片')], 'g13', '变体')
add('open the baike entry', BING_S, POS,
    [B('link', '返回到必应搜索'),
     B('searchbox', '在此处输入你的搜索 — 输入时会显示搜索建议'),
     B('button', '跳至内容')], 'g13', 'en 变体')

# ── G14 weather 40天趋势（B_recall：link 40天 被裁，top1=新闻长链）──
POS = B('link', '40天')
add('切换到40天天气趋势', WEATHER, POS,
    [B('link', '8-15天'), B('link', '7天'), B('link', '今天')],
    'g14', '周期 tab 兄弟——数字粒度判别')
add('看40天的天气趋势', WEATHER, POS,
    [B('link', '8-15天'), B('link', '7天'), B('link', '首页')],
    'g14', '变体')
add('open the 40-day forecast', WEATHER, POS,
    [B('link', '7天'), B('link', '今天'), B('link', '预报')],
    'g14', 'en 变体')

# ── quarantine（归因层标注口径矛盾，不猜标）──
QUAR_PRE = [
    {'query': 'open the comments link of a story',
     'page': 'https://news.ycombinator.com/newest',
     'reason': 'T.name 含 link:comments(nav) 且 top1=comments 已命中判据——'
              '伪 miss/标注口径矛盾；per-story discuss 与 nav comments '
              '两可，不猜标'},
    {'query': '按时长筛选视频',
     'page': 'https://cn.bing.com/videos?q=大熊猫',
     'reason': 'T.syn 含 link:短视频 且 top1=短视频 已命中判据——'
              '伪 miss；"时长筛选"语义目标(button 筛选器)与判据目标'
              '(link 视频)冲突，不猜标'},
]


def main():
    with OUT.open('w', encoding='utf-8') as f:
        for r in ROWS:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    with OUT_Q.open('w', encoding='utf-8') as f:
        for r in QUAR_PRE + QUAR:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(f'rows={len(ROWS)} quar={len(QUAR_PRE) + len(QUAR)} → {OUT}')
    for r in QUAR:
        print(f"  gate-quar: {r['query']} | {r['reason']}")
    for r in QUAR_PRE:
        print(f"  pre-quar : {r['query']} | {r['reason'][:60]}")


if __name__ == '__main__':
    main()
