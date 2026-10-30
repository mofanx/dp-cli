#!/usr/bin/env python3
"""zh→en 跨语定向教师标注（本 agent 即教师，无外部 API）。

动机：eval-120 miss 族谱显示 zh goal 配 en 候选名的跨语映射是
模型弱面（登录→Sign In / 深色模式→Dark mode / 搜索→Search 类）。
数据源：
  真机锚定——~/.dp_cli/hard_cases.jsonl 里 zh goal + en 候选的
    跨语形态行（fixture login/form/nav/product/settings +
    github.com 实站），正例由本 agent 按语义标注，negatives 取
    同池干扰；
  合成补齐——fixture.zhen/* 独立命名空间 en 候选页，同判别难点
    不同实体/表述，凑足 ~40 行。

prov='teacher_zhen'（弱标注级——无执行后证据，merge/train 须
显式 --allow-unverified，与 r4/eval120_miss_r4 同口径）。

三泄漏闸（沿 r1/r2/r3 口径）：
  闸1 query×全部 120 eval goal exact+子串（merge_train._hit）；
  闸2 miss-eval 候选串零复用（历史 miss id 并集全量候选串，
      pos+neg 双查）；
  闸3 query 去重（组内 + vs merge_out_v24）+ pos∉negs + pos 非空。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from eval_cases import EVAL_CASES            # noqa: E402
from bench_engines import rich_name          # noqa: E402
from merge_train import _hit                 # noqa: E402

OUT = Path(__file__).parent / 'teacher_zhen.jsonl'
OUT_ARR = Path(__file__).parent / 'teacher_zhen.json'
OUT_Q = Path(__file__).parent / 'teacher_zhen_quarantine.jsonl'
V24 = Path(__file__).parent / 'merge_out_v24.json'

# 历史 miss id 并集（r3 31 id 口径）——候选串零复用
MISS_IDS = {"t06", "c10", "n05", "n08", "n22", "m11", "m12", "m13",
            "m14", "m15", "m17", "m25", "m26", "m27", "m29", "m30",
            "x01", "x03", "x04", "x09", "x12", "x15", "x18", "x43",
            "x50"}
EVAL_GOALS = {c['goal'].strip() for c in EVAL_CASES}
FORBIDDEN = {f"{o['role']} {rich_name(o)}"
             for c in EVAL_CASES if c['id'] in MISS_IDS
             for o in c['options']}

V24_QUERIES = {r['query'].strip()
               for r in json.loads(V24.read_text(encoding='utf-8'))}

ROWS, QUAR, SEEN_Q = [], [], set()


def add(query, page, pos, negs, slug, note):
    qs = query.strip()
    # 闸1 query×eval goal exact+子串
    if _hit(qs, EVAL_GOALS):
        QUAR.append({'query': query, 'slug': slug,
                     'reason': 'gate1: query 命中 eval goal'})
        return
    # 闸2 miss-eval 候选串零复用（pos+neg 双查）
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
    ROWS.append({'src': 'teacher_hardneg', 'prov': 'teacher_zhen',
                 'page': page, 'goal': query, 'query': query,
                 'positive': pos, 'negatives': list(negs),
                 'candidates': [pos] + list(negs),
                 'teacher_note': f'{slug}: {note}'})


def B(role, name):
    return f'{role} {name}'


FIX = 'file:///home/yan/ubuntu/project/github/dp-cli/bench/fixtures'
LOGIN = f'{FIX}/login.html'
FORM = f'{FIX}/form.html'
NAV = f'{FIX}/nav.html'
PRODUCT = f'{FIX}/product.html'
SETTINGS = f'{FIX}/settings.html'
GITHUB = 'https://github.com/'


def syn(slug):
    return f'https://fixture.zhen/{slug}'


# ══ A. 真机锚定（hard_cases zh goal→en 候选跨语形态）════════════
# ── A1 login.html（真机行 goal：点击登录按钮/填写用户名）──
add('点击登录按钮', LOGIN, B('button', 'Sign In'),
    [B('textbox', 'Username'), B('textbox', 'Password'),
     B('link', 'Create account')], 'a1',
    '真机行：zh 登录意图→en Sign In 按钮非表单项')
add('按下登录键', LOGIN, B('button', 'Sign In'),
    [B('link', 'Forgot password?'), B('checkbox', 'Remember me'),
     B('textbox', 'Password')], 'a1', '同型变体')
add('填写用户名', LOGIN, B('textbox', 'Username'),
    [B('textbox', 'Password'), B('button', 'Sign In'),
     B('checkbox', 'Remember me')], 'a1',
    '真机行：zh 用户名→en Username 字段')
add('输入登录密码', LOGIN, B('textbox', 'Password'),
    [B('textbox', 'Username'), B('button', 'Sign In'),
     B('link', 'Forgot password?')], 'a1', '密码字段对称锚点')
add('勾选记住我选项', LOGIN, B('checkbox', 'Remember me'),
    [B('button', 'Sign In'), B('link', 'Forgot password?'),
     B('textbox', 'Username')], 'a1', 'checkbox vs 按钮判别')
add('找回登录密码', LOGIN, B('link', 'Forgot password?'),
    [B('link', 'Create account'), B('button', 'Sign In'),
     B('textbox', 'Username')], 'a1', 'link vs button 判别')
add('创建一个新账号', LOGIN, B('link', 'Create account'),
    [B('button', 'Sign In'), B('link', 'Forgot password?'),
     B('textbox', 'Username')], 'a1', '注册→Create account 映射')

# ── A2 form.html（真机行 goal：填写邮箱地址）──
add('填写邮箱地址', FORM, B('textbox', 'Email'),
    [B('textbox', 'Full name'), B('textbox', 'Phone'),
     B('button', 'Submit')], 'a2',
    '真机行：zh 邮箱→en Email 字段（Phone 同族干扰）')
add('填写手机号码', FORM, B('textbox', 'Phone'),
    [B('textbox', 'Email'), B('textbox', 'Full name'),
     B('combobox', 'Country')], 'a2', '手机→Phone 映射')
add('填姓名全称', FORM, B('textbox', 'Full name'),
    [B('textbox', 'Email'), B('textbox', 'Phone'),
     B('checkbox', 'I agree to terms')], 'a2', '姓名→Full name 映射')
add('提交注册表单', FORM, B('button', 'Submit'),
    [B('checkbox', 'I agree to terms'), B('combobox', 'Country'),
     B('textbox', 'Email')], 'a2', '提交→Submit 按钮非输入项')
add('选择所在国家', FORM, B('combobox', 'Country'),
    [B('textbox', 'Phone'), B('checkbox', 'I agree to terms'),
     B('button', 'Submit')], 'a2', '国家→combobox role 判别')
add('同意服务条款', FORM, B('checkbox', 'I agree to terms'),
    [B('button', 'Submit'), B('combobox', 'Country'),
     B('textbox', 'Email')], 'a2', '条款→checkbox 非提交钮')

# ── A3 nav.html（真机行 goal：搜索本站内容）──
add('搜索本站内容', NAV, B('searchbox', 'Search site'),
    [B('link', 'Blog'), B('link', 'Products'), B('link', 'Contact')],
    'a3', '真机行：zh 搜索→en searchbox 非导航 link')
add('查找站内文章', NAV, B('searchbox', 'Search site'),
    [B('link', 'Blog'), B('link', 'Home'), B('link', 'Pricing')],
    'a3', '同型变体')
add('进入博客频道', NAV, B('link', 'Blog'),
    [B('link', 'Products'), B('link', 'Pricing'),
     B('searchbox', 'Search site')], 'a3', '导航 link 判别')
add('打开产品页', NAV, B('link', 'Products'),
    [B('link', 'Pricing'), B('link', 'Home'),
     B('searchbox', 'Search site')], 'a3', '产品→Products 映射')
add('查看产品价格', NAV, B('link', 'Pricing'),
    [B('link', 'Products'), B('link', 'Blog'),
     B('link', 'Contact')], 'a3', '价格→Pricing 映射')
add('联系网站客服', NAV, B('link', 'Contact'),
    [B('link', 'Blog'), B('link', 'Products'),
     B('searchbox', 'Search site')], 'a3', '联系→Contact 映射')

# ── A4 product.html（真机行 goal：立即购买下单）──
add('立即购买下单', PRODUCT, B('button', 'Buy now'),
    [B('button', 'Add to cart'), B('spinbutton', 'Qty'),
     B('link', 'Reviews (128)')], 'a4',
    '真机行：zh 购买→en Buy now 非加购')
add('先加购物车', PRODUCT, B('button', 'Add to cart'),
    [B('button', 'Buy now'), B('spinbutton', 'Qty'),
     B('link', 'Reviews (128)')], 'a4',
    '加购↔购买对称锚点——方向判别')
add('看看用户评价', PRODUCT, B('link', 'Reviews (128)'),
    [B('button', 'Buy now'), B('button', 'Add to cart'),
     B('spinbutton', 'Qty')], 'a4', '评价→Reviews link')

# ── A5 settings.html（真机行 goal：开启深色模式）──
add('开启深色模式', SETTINGS, B('checkbox', 'Dark mode'),
    [B('checkbox', 'Enable notifications'), B('button', 'Save changes'),
     B('button', 'Reset to defaults')], 'a5',
    '真机行：zh 深色→en Dark mode checkbox')
add('打开消息通知', SETTINGS, B('checkbox', 'Enable notifications'),
    [B('checkbox', 'Dark mode'), B('button', 'Save changes'),
     B('radio', 'English')], 'a5', '通知→notifications 映射')
add('保存当前设置', SETTINGS, B('button', 'Save changes'),
    [B('button', 'Reset to defaults'), B('checkbox', 'Dark mode'),
     B('radio', 'English')], 'a5', '保存→Save 按钮非 checkbox')
add('恢复默认设置', SETTINGS, B('button', 'Reset to defaults'),
    [B('button', 'Save changes'), B('checkbox', 'Enable notifications'),
     B('radio', '中文')], 'a5', '重置↔保存对称锚点')
add('切换成英文界面', SETTINGS, B('radio', 'English'),
    [B('radio', '中文'), B('button', 'Save changes'),
     B('checkbox', 'Dark mode')], 'a5', '语言→radio role 判别')

# ── A6 github.com（真机行 goal：点击 Sign in 登录/注册 GitHub 账号）──
add('点击 Sign in 登录', GITHUB, B('link', 'Sign in'),
    [B('button', 'Sign up for GitHub'), B('link', 'GitHub')], 'a6',
    '真机行：Sign in link vs Sign up button——in/up 判别')
add('注册 GitHub 账号', GITHUB, B('button', 'Sign up for GitHub'),
    [B('link', 'Sign in'), B('link', 'GitHub')], 'a6',
    '真机行：注册→Sign up 非 Sign in（镜像对称）')
add('登录我的 GitHub', GITHUB, B('link', 'Sign in'),
    [B('button', 'Sign up for GitHub'), B('link', 'GitHub')],
    'a6', '同型变体')

# ══ B. 合成补齐（fixture.zhen/* en 候选页，同难点异实体）═════
add('把界面调成深色', syn('appearance'), B('switch', 'Dark theme'),
    [B('button', 'Apply'), B('link', 'Appearance help'),
     B('checkbox', 'High contrast')], 'b1',
    '深色→Dark theme switch（role 泛化非 checkbox 唯一）')
add('订阅每周邮件简报', syn('newsletter'),
    B('checkbox', 'Weekly newsletter'),
    [B('textbox', 'Email address'), B('button', 'Subscribe now'),
     B('link', 'Sample issue')], 'b2', '订阅→newsletter checkbox')
add('把商品移入收藏夹', syn('shop'), B('button', 'Move to wishlist'),
    [B('button', 'Add to cart'), B('link', 'Compare'),
     B('spinbutton', 'Qty')], 'b3', '收藏→wishlist 非加购')
add('下载上月账单', syn('billing'), B('link', 'Download invoice'),
    [B('link', 'Billing history'), B('button', 'Pay now'),
     B('link', 'Usage details')], 'b4', '账单→invoice link')
add('上传头像图片', syn('account'), B('button', 'Upload photo'),
    [B('link', 'Choose image'), B('textbox', 'Display name'),
     B('button', 'Remove photo')], 'b5', '上传→Upload button')
add('填写收货地址', syn('checkout'), B('textbox', 'Shipping address'),
    [B('textbox', 'City'), B('combobox', 'Province'),
     B('textbox', 'ZIP code')], 'b6', '收货→Shipping 映射')
add('输入邮政编码', syn('checkout2'), B('textbox', 'ZIP code'),
    [B('textbox', 'City'), B('textbox', 'Shipping address'),
     B('combobox', 'Province')], 'b6', '邮编→ZIP 映射')
add('暂停当前播放', syn('player'), B('button', 'Pause'),
    [B('button', 'Play'), B('link', 'Lyrics'),
     B('button', 'Full screen')], 'b7', '暂停↔播放对称')
add('打开全屏模式', syn('player2'), B('button', 'Full screen'),
    [B('button', 'Pause'), B('link', 'Mini player'),
     B('button', 'Volume')], 'b7', '全屏→Full screen')
add('查看未读通知', syn('inbox'), B('link', 'Notifications'),
    [B('link', 'Messages'), B('button', 'Mark all as read'),
     B('link', 'Archive')], 'b8', '通知→Notifications')
add('退出当前账户', syn('account2'), B('link', 'Log out'),
    [B('link', 'Profile'), B('button', 'Switch account'),
     B('link', 'Dashboard')], 'b9', '退出→Log out link')
add('修改个人资料', syn('profile'), B('link', 'Edit profile'),
    [B('link', 'Account settings'), B('button', 'Save'),
     B('link', 'Avatar')], 'b10', '资料→profile link')
add('给客服发消息', syn('support'), B('button', 'Contact support'),
    [B('link', 'Help center'), B('textbox', 'Message'),
     B('link', 'FAQ')], 'b11', '客服→Contact support')
add('选中只看现货', syn('catalog'), B('checkbox', 'In stock only'),
    [B('combobox', 'Sort order'), B('link', 'Clear filters'),
     B('link', 'Best sellers')], 'b12', '现货→In stock checkbox')
add('按销量排序商品', syn('catalog2'), B('link', 'Best sellers'),
    [B('combobox', 'Sort order'), B('checkbox', 'In stock only'),
     B('link', 'New arrivals')], 'b12', '销量→Best sellers')
add('确认收货地址无误', syn('confirm'), B('button', 'Confirm address'),
    [B('textbox', 'Shipping address'), B('link', 'Edit address'),
     B('button', 'Back to cart')], 'b13', '确认→Confirm button')
add('输入邀请码', syn('invite'), B('textbox', 'Invite code'),
    [B('button', 'Redeem'), B('link', 'Get invite'),
     B('textbox', 'Email')], 'b14', '邀请码→Invite code field')


def main():
    with OUT.open('w', encoding='utf-8') as f:
        for r in ROWS:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    OUT_ARR.write_text(json.dumps(ROWS, ensure_ascii=False),
                       encoding='utf-8')
    with OUT_Q.open('w', encoding='utf-8') as f:
        for r in QUAR:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(f'rows={len(ROWS)} quar={len(QUAR)} → {OUT}')
    for r in QUAR:
        print(f"  gate-quar: {r['query']} | {r['reason']}")


if __name__ == '__main__':
    main()
