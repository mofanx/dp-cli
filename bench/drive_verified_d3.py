#!/usr/bin/env python3
"""Real-site verified_real capture for click/navigation tasks (t2/d3).

Each row carries a natural-language goal, pre-action refs, the executed
action, and an outcome proof: the same eval expr evaluated before and
after the action must cross from `old != expected` to `new == expected`.

Only public demo sites, read-only/click interactions. No login, no
purchase, no real form submission.

Run with TMPDIR on a writable disk if /tmp has a user quota.
"""
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dp_cli.session import load_refs
from verified_data import has_outcome_evidence, eligible

OUT = ROOT / 'bench' / 'drive_verified_d3.jsonl'

TI = 'https://the-internet.herokuapp.com'
BT = 'https://books.toscrape.com'
DQ = 'https://demoqa.com'

# (goto_url|None=stay, goal, role, name|None, cmd_argv, eval_expr, expected,
#  wait_seconds, pick_index)
TASKS = [
    (TI + '/', '进入 A/B Testing 页面', 'link', 'A/B Testing',
     ('click',), 'location.pathname', '/abtest', 0),
    (TI + '/', '进入 Add/Remove Elements 页面', 'link', 'Add/Remove Elements',
     ('click',), 'location.pathname', '/add_remove_elements/', 0),
    (None, '点 Add Element 添加一个元素', 'button', 'Add Element',
     ('click',), "document.querySelectorAll('.added-manually').length", 1, 0),
    (None, '再点 Add Element 添加第二个元素', 'button', 'Add Element',
     ('click',), "document.querySelectorAll('.added-manually').length", 2, 0),
    (None, '点 Delete 删掉一个元素', 'button', 'Delete',
     ('click',), "document.querySelectorAll('.added-manually').length", 1, 0),
    (TI + '/checkboxes', '勾选第一个复选框', 'checkbox', None,
     ('click',), "document.querySelectorAll('#checkboxes input')[0].checked", True, 0, 0),
    (None, '取消勾选第二个复选框', 'checkbox', None,
     ('click',), "document.querySelectorAll('#checkboxes input')[1].checked", False, 0, 1),
    (TI + '/inputs', '在数字输入框填入 42', 'spinbutton', None,
     ('fill', '42'), "document.querySelector('input[type=number]').value", '42', 0),
    (TI + '/forgot_password', '在找回密码邮箱框填入测试邮箱', 'textbox', 'E-mail',
     ('fill', 'x@y.z'), "document.querySelector('#email').value", 'x@y.z', 0),
    (TI + '/notification_message_rendered', '点 Click here 触发通知消息', 'link', 'Click here',
     ('click',), "!!document.querySelector('.flash')", True, 4),
    (TI + '/dynamic_loading/1', '点 Start 等待隐藏元素显示', 'button', 'Start',
     ('click',),
     "(()=>{let e=document.getElementById('finish');return !!(e&&getComputedStyle(e).display!=='none')})()",
     True, 15),
    (TI + '/status_codes', '打开 200 状态码页面', 'link', '200',
     ('click',), 'location.pathname', '/status_codes/200', 0),
    (DQ + '/buttons', '点 Click Me 触发动态点击消息', 'button', 'Click Me',
     ('click',), "document.querySelector('#dynamicClickMessage')?.textContent || ''",
     'You have done a dynamic click', 0),
    (DQ + '/radio-button', '选择 Yes 单选按钮', 'radio', 'Yes',
     ('click',), "document.querySelector('.text-success')?.textContent || ''", 'Yes', 0),
    (None, '选择 Impressive 单选按钮', 'radio', 'Impressive',
     ('click',), "document.querySelector('.text-success')?.textContent || ''", 'Impressive', 0),
    (BT + '/', '进入 Travel 分类', 'link', 'Travel',
     ('click',), 'location.pathname', '/catalogue/category/books/travel_2/index.html', 0),
    (BT + '/', '翻到第二页', 'link', 'next',
     ('click',), 'location.pathname', '/catalogue/page-2.html', 0),
    ('https://books.toscrape.com/catalogue/page-2.html', '加入第一本书到购物篮', 'button', 'Add to basket',
     ('click',),
     "(()=>{let e=document.querySelector('.basket-mini strong');return e?parseFloat(e.textContent.replace('£',''))>0:false})()",
     True, 0, 0),
]



TASKS += [
    (TI + '/frames', '进入 iFrame 页', 'link', 'iFrame',
     ('click',), 'location.pathname', '/iframe', 0),
    (TI + '/redirector', '点 here 触发重定向到状态码页', 'link', 'here',
     ('click',), 'location.pathname', '/status_codes', 0),
    (TI + '/status_codes', '打开 301 状态码页面', 'link', '301',
     ('click',), 'location.pathname', '/status_codes/301', 0),
    (TI + '/status_codes', '打开 404 状态码页面', 'link', '404',
     ('click',), 'location.pathname', '/status_codes/404', 0),
    (TI + '/dynamic_controls', '点 Remove 移除复选框', 'button', 'Remove',
     ('click',), "document.querySelector('#checkbox')===null", True, 8),
    (TI + '/dynamic_controls', '点 Enable 启用输入框', 'button', 'Enable',
     ('click',), "document.querySelector('#input-example input').disabled===false", True, 8),
    (TI + '/dynamic_loading/2', '点 Start 等待元素渲染出来', 'button', 'Start',
     ('click',), "!!document.getElementById('finish')", True, 15),
    (TI + '/key_presses', '在按键框填入 A', 'textbox', None,
     ('fill', 'A'), "document.querySelector('#target').value", 'A', 0),
    (DQ + '/links', '点 Created 链接验证 201 响应', 'link', 'Created',
     ('click',),
     "document.querySelector('#linkResponse')?.textContent || ''",
     'Link has responded with staus 201 and status text Created', 5),
    (DQ + '/links', '点 Moved 链接验证 301 响应', 'link', 'Moved',
     ('click',),
     "document.querySelector('#linkResponse')?.textContent || ''",
     'Link has responded with staus 301 and status text Moved Permanently', 5),
    (DQ + '/modal-dialogs', '点 Small modal 打开小弹窗', 'button', 'Small modal',
     ('click',), "!!document.querySelector('#example-modal-sizes-title-sm')", True, 3),
    (DQ + '/tabs', '切换到 Origin 标签页', 'tab', 'Origin',
     ('click',), "document.querySelector('#demo-tab-origin')?.getAttribute('aria-selected')", 'true', 3),
    (DQ + '/webtables', '点 Add 打开登记表单弹窗', 'button', 'Add',
     ('click',), "!!document.querySelector('#registration-form-modal')", True, 3),
    (DQ + '/text-box', '在全名框填入测试名字', 'textbox', 'Full Name',
     ('fill', '李四'), "document.querySelector('#userName').value", '李四', 0),
    (DQ + '/text-box', '在邮箱框填入测试邮箱', 'textbox', 'name@example.com',
     ('fill', 'lisi@x.com'), "document.querySelector('#userEmail').value", 'lisi@x.com', 0),
    (BT + '/', '进入 Mystery 分类', 'link', 'Mystery',
     ('click',), 'location.pathname', '/catalogue/category/books/mystery_3/index.html', 0),
    (BT + '/catalogue/category/books/travel_2/index.html', '打开 A Light in the Attic 详情', 'link', 'A Light in the Attic',
     ('click',), "document.querySelector('h1').textContent", 'A Light in the Attic', 0),
    (BT + '/catalogue/category/books/mystery_3/index.html', '在 Mystery 分类翻到第二页', 'link', 'next',
     ('click',), 'location.pathname', '/catalogue/category/books/mystery_3/page-2.html', 0),
    (BT + '/catalogue/a-light-in-the-attic_1000/index.html', '点 All products 回目录', 'link', 'All products',
     ('click',), 'location.pathname', '/catalogue/index.html', 0),
    (TI + '/sortable_data_tables', '点 Last Name 表头排序', 'button', 'Last Name',
     ('click',), "document.querySelector('#table1 tbody tr td').textContent", 'Bach', 3),
]

def call(session, *args):
    cmd = ['dpx', *args, '--session', session]
    env = dict(os.environ, DPX_RECORD_DECIDE='0', DPX_NO_DAEMON='1')
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=90, env=env)
    try:
        response = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError(f'{args[0]} returned non-JSON (exit {proc.returncode}): '
                           f'{proc.stdout[:200]!r}')
    if proc.returncode != 0 or response.get('status') != 'ok':
        raise RuntimeError(f'{args[0]} failed: {response.get("code", "UNKNOWN")} '
                           f'{response.get("message", "")[:100]}')
    return response


def eval_raw(session, expr):
    cmd = ['dpx', 'eval', expr, '--session', session]
    env = dict(os.environ, DPX_RECORD_DECIDE='0', DPX_NO_DAEMON='1')
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=90, env=env)
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {'status': 'error', 'code': 'NON_JSON'}


def snapshot(session):
    proc = subprocess.run(['dpx', 'snapshot', '--session', session],
                          capture_output=True, text=True, timeout=90,
                          env=dict(os.environ, DPX_RECORD_DECIDE='0',
                                   DPX_NO_DAEMON='1'))
    if proc.returncode != 0:
        raise RuntimeError(f'snapshot failed (exit {proc.returncode})')
    return load_refs(session)


def capture(session, task):
    goto, goal, role, name, command, expr, expected, wait = task[:8]
    if goto is not None:
        call(session, 'goto', goto)
        time.sleep(1.5)  # real-site settle
    refs = snapshot(session)
    options = [
        {'ref': int(k), 'role': v.get('role', ''), 'name': v.get('name', '')}
        for k, v in refs.items() if str(k).isdigit()
    ]
    pick = task[8] if len(task) > 8 else 0
    selected = [o for o in options
                if o['role'] == role and (name is None or o['name'] == name)]
    if len(selected) <= pick:
        raise RuntimeError(f'expected >{pick} {role} {name!r}, got {len(selected)}')
    chosen = selected[pick]
    ref = chosen['ref']
    def _run_step(step):
        refs2 = snapshot(session)
        opts2 = [{'ref': int(k), 'role': v.get('role', ''), 'name': v.get('name', '')}
                 for k, v in refs2.items() if str(k).isdigit()]
        sel2 = [o for o in opts2 if o['role'] == step['role']
                and (step['name'] is None or o['name'] == step['name'])]
        if len(sel2) <= step.get('pick', 0):
            raise RuntimeError(f"step ref {step['role']} {step['name']!r} not found")
        r2 = sel2[step.get('pick', 0)]['ref']
        call(session, step['cmd'][0], f'ref:{r2}', *step['cmd'][1:])
        time.sleep(0.8)

    # 多步流前置步：主动作前的准备动作（如先 fill 再 submit）
    for step in (task[10] if len(task) > 10 else ()):
        _run_step(step)
    url = eval_raw(session, 'location.href')['data']['result']
    before = call(session, 'eval', expr)
    old = before['data']['result']
    if old == expected:
        raise RuntimeError(f'pre-state already satisfies outcome ({old!r})')
    argv = [command[0], f'ref:{ref}', *command[1:]]
    action = call(session, *argv)
    # 多步流：主动作后顺序执行 task[9] 步（每步重新解析 ref）
    steps = task[9] if len(task) > 9 else ()
    for step in steps:
        _run_step(step)
    if wait:
        deadline = time.time() + wait
        after = eval_raw(session, expr)
        while (after.get('data', {}).get('result') != expected
               and time.time() < deadline):
            time.sleep(1.0)
            after = eval_raw(session, expr)
    else:
        after = eval_raw(session, expr)
    render = lambda o: f"{o['role']} {o['name']}".strip()
    row = {
        'src': 'verified_real',
        'query': goal,
        'positive': render(chosen),
        'negatives': [render(o) for o in options if o['ref'] != ref],
        'page': url,
        'evidence': {
            'recorded_at': datetime.now(timezone.utc).isoformat(),
            'session': session,
            'url': url,
            'before_refs': options,
            'chosen_ref': ref,
            'before': {'expr': expr, 'result': before},
            'action': {'argv': argv, 'exit': 0,
                       'result': {'status': action['status']}},
            'after': {'expr': expr, 'expected': expected, 'result': after},
        },
    }
    if not has_outcome_evidence(row):
        raise RuntimeError(f'outcome proof failed: after={after.get("data", {}).get("result")!r} '
                           f'expected={expected!r}')
    if not eligible(row):
        raise RuntimeError('eligible() rejected')
    return row


# ── TASK_IDX=auto：现场看 refs 生成任务（页面意图+用户视角 goal，非「点击{role}」复读）──
AUTO_PAGES = [u for u in os.environ.get('AUTO_PAGES', '').split(',') if u] \
    or [f'http://127.0.0.1:8901/deep/p{i:02d}.html' for i in range(1, 25)]

# name→用户意图改写（fixture 语义跳跃名；fallback 走 role 意图句式但打散措辞）
_INTENT = {
    '去结算': ['我要结账付款', '帮我完成下单'],
    '👍 顶一下': ['给这条内容点赞', '喜欢这个帖子'],
    '☆ 收藏': ['把这篇存起来以后看', '收藏这篇文章备用'],
    '← 上一页': ['回到列表页', '退回上一页看看'],
    '⚙ 偏好设置': ['打开设置面板', '调一下偏好选项'],
    '🔍': ['搜一下这个商品', '用它搜个东西'],
    '×': ['关闭这个弹窗', '把弹窗关掉'],
    'Your comment': ['在评论区写下你的看法', '留言说两句'],
    '获取文件': ['下载这份报表', '把文件拿下来'],
    '家': ['选择收货地址二', '用家里的地址收货'],
    '安全退出': ['退出当前账号登录', '从账号里退出去'],
    '了解更多': ['查看产品详情', '看看这东西的详情'],
    '往后翻': ['翻到下一页', '看下一页内容'],
    '确认无误': ['提交订单', '确认这单没问题提交吧'],
    '↻': ['刷新当前页面数据', '重新拉一下数据'],
    '?': ['查看使用帮助', '看下这功能怎么用'],
    '推荐好友': ['把这个页面分享出去', '推荐给朋友看看'],
    '☑ 全部': ['选中列表里所有条目', '把清单全勾上'],
    '加入书签': ['收藏这个地址', '把链接存书签里'],
    '免费试用': ['开通会员', '试一下会员功能'],
    '忘记密码': ['找回我的密码', '密码忘了怎么办'],
    '移出清单': ['把商品移出清单', '这个不要了删出去'],
    '归档': ['别点删除点归档', '这条邮件先归档'],
    'To': ['搜飞东京的航班', '目的地填东京查航班'],
    # biz 业务台（goal 与 name 零重叠——避开 x-系 eval 措辞）
    'Create Ticket': ['新报一张报障单', '提个故障单子上去'],
    '批量冲红': ['把这几张发票红冲掉'],
    '不予通过': ['退回这份请假申请'],
    'Mute': ['把这个通知静音一小时'],
    'Acknowledge': ['认领这条告警'],
    'Del': ['清掉这一行'],
    '运单轨迹': ['看快递走到哪'],
    '新建报销单': ['把打车钱报了'],
    '申请调班': ['我想换个班次'],
    'Rolling restart': ['滚动重启这个实例'],
    '邀请协作者': ['拉同事一起改'],
    '安排面试': ['约候选人面谈'],
    '低库存筛选': ['只看家底薄的货'],
    '开始投递': ['把群发邮件发掉'],
    '分配至-华东': ['派给上海组'],
}
_ROLE_INTENT = {
    'link': '打开{name}对应的页面',
    'button': '执行{name}这个操作',
    'textbox': '在{name}里写点东西',
    'checkbox': '把{name}勾上',
    'radio': '选{name}这个选项',
}


def _js_get(session, expr):
    """eval_raw 取 data.result——auto 模式探测元素属性用。"""
    return eval_raw(session, expr).get('data', {}).get('result')


import re as _re
_EVAL_GOALS = [None]
def _norm(s):
    return _re.sub(r'[\s，。、！？""'']+', '', (s or '').lower())

def _eval_goals():
    """eval_cases 全 goal 规范化集——auto 生成防撞前置（后拦截不如不产）。"""
    if _EVAL_GOALS[0] is None:
        sys.path.insert(0, str(Path(__file__).parent))
        from eval_cases import EVAL_CASES
        _EVAL_GOALS[0] = {_norm(c['goal']) for c in EVAL_CASES}
    return _EVAL_GOALS[0]


def auto_tasks(session, page_url):
    """现场生成 (goal,ref-名,action,断言) 任务——断言值从页面属性实取非猜。"""
    tasks = []
    try:
        call(session, 'goto', page_url)
        time.sleep(1.0)
        refs = snapshot(session)
    except Exception as e:
        print(f'auto goto/snapshot fail {page_url}: {e}', flush=True)
        return tasks
    opts = [{'ref': int(k), 'role': v.get('role', ''),
             'name': v.get('name', '')}
            for k, v in refs.items() if str(k).isdigit()]
    hrefs = _js_get(session,
        "[...document.querySelectorAll('a')].map(a=>[a.textContent.trim(),a.getAttribute('href')])") or []
    onclick = _js_get(session,
        "[...document.querySelectorAll('button')].map(b=>[b.textContent.trim(),b.getAttribute('onclick')])") or []
    link_href = {n: h for n, h in hrefs if h}
    btn_oc = {n: oc for n, oc in onclick if oc}
    # button aria 状态探针（无 onclick 按钮的可确定断言子集）
    btn_attrs = _js_get(session,
        "[...document.querySelectorAll('button')].map(b=>[b.textContent.trim(),"
        "b.getAttribute('aria-pressed'),b.getAttribute('aria-expanded'),"
        "b.getAttribute('aria-controls')])") or []
    # form GET 提交探针（多步 fill→submit→导航断言）
    forms = _js_get(session,
        "[...document.querySelectorAll('form')].map(f=>{"
        "const inp=[...f.querySelectorAll('input[type=text],input:not([type]),"
        "input[type=search],textarea')][0];"
        "const sub=[...f.querySelectorAll('button[type=submit],input[type=submit],"
        "button')][0];"
        "return[f.getAttribute('action')||'',(f.method||'get').toLowerCase(),"
        "inp?(inp.placeholder||inp.getAttribute('aria-label')||inp.name||inp.id||''):'',"
        "sub?(sub.textContent.trim()||sub.value||''):'']})") or []
    forms_done = False

    for o in opts:
        role, name = o['role'], o['name']
        goals = _INTENT.get(name) or [_ROLE_INTENT.get(role, '').format(name=name)]
        goal = next((g for g in goals if g and _norm(g) not in _eval_goals()),
                    None)
        if not goal:
            continue
        try:
            if role == 'link':
                href = link_href.get(name, '')
                if href and not href.startswith(('#', 'javascript')):
                    from urllib.parse import urljoin, urlparse
                    p = urlparse(urljoin(page_url, href))
                    if p.path in ('', '/', urlparse(page_url).path):
                        tasks.append((page_url, goal, role, name, ('click',),
                                      'location.href', p.geturl(), 0, 0))
                    else:
                        tasks.append((page_url, goal, role, name, ('click',),
                                      'location.pathname', p.path, 0, 0))
            elif role == 'button':
                oc = btn_oc.get(name, '')
                import re
                m = re.search(r"textContent\s*=\s*'([^']+)'", oc)
                if m:  # fixture 的 st 断言钩子——无钩子按钮跳过（断言不可造）
                    tasks.append((page_url, goal, role, name, ('click',),
                                  "document.getElementById('st').textContent",
                                  m.group(1), 0, 0))
                    continue
                # aria 状态翻转断言（无 onclick 按钮的可确定闭合子集）
                nm_js = name.replace("'", "\\'")
                sel = (f"[...document.querySelectorAll('button')]"
                       f".find(b=>b.textContent.trim()==='{nm_js}')")
                for bn, ap, ae, ac in btn_attrs:
                    if bn != name:
                        continue
                    if ap in ('true', 'false'):
                        cur = _js_get(session, f"{sel}.getAttribute('aria-pressed')")
                        if cur in ('true', 'false'):
                            tasks.append((page_url, goal, role, name, ('click',),
                                          f"{sel}.getAttribute('aria-pressed')",
                                          'false' if cur == 'true' else 'true', 0, 0))
                    elif ae in ('true', 'false'):
                        cur = _js_get(session, f"{sel}.getAttribute('aria-expanded')")
                        if cur in ('true', 'false'):
                            tasks.append((page_url, goal, role, name, ('click',),
                                          f"{sel}.getAttribute('aria-expanded')",
                                          'false' if cur == 'true' else 'true', 0, 0))
                    elif ac:
                        cur = _js_get(session,
                            f"document.getElementById('{ac}')?.offsetHeight>0")
                        if isinstance(cur, bool):
                            tasks.append((page_url, goal, role, name, ('click',),
                                          f"document.getElementById('{ac}').offsetHeight>0",
                                          not cur, 0, 0))
            elif role == 'textbox':
                tasks.append((page_url, goal, role, name, ('fill', 'auto-fx'),
                              f"[...document.querySelectorAll('input')].find(i=>i.placeholder==='{name}')?.value",
                              'auto-fx', 0, 0))
            elif role in ('checkbox', 'radio'):
                idx_js = ("[...document.querySelectorAll('label')]"
                          f".findIndex(l=>l.textContent.trim().endsWith('{name}'))")
                pos = _js_get(session, idx_js)
                if isinstance(pos, int) and pos >= 0:
                    tasks.append((page_url, goal, role, name, ('click',),
                                  f"document.querySelectorAll('input[type={role}]')[{pos}].checked",
                                  True, 0, 0))
        except Exception:
            continue
    # 多步流：form GET fill→submit→导航断言（每页最多 1 个）
    if not forms_done:
        from urllib.parse import urljoin, urlparse
        cur_path = urlparse(page_url).path
        for action, method, inp_name, sub_name in forms:
            if forms_done or method != 'get' or not inp_name:
                continue
            if action and action != '#':
                p = urlparse(urljoin(page_url, action))
                if p.path not in ('', cur_path):
                    expr, expected = 'location.pathname', p.path
                else:
                    expr, expected = 'location.search.length>0', True
            else:
                continue  # 无 action 的 JS 表单不可预测——跳过
            goal = f'填写{inp_name}并提交' if inp_name else '填完表单提交'
            tasks.append((page_url, goal, 'button', sub_name or None,
                          ('click',), expr, expected, 2, 0,
                          [],  # post steps
                          [{'cmd': ('fill', 'auto-fx'), 'role': 'textbox',
                            'name': inp_name or None, 'pick': 0}]))
            forms_done = True
    return tasks[:8]


def main():
    session = 'verified_d3_' + uuid.uuid4().hex[:10]
    rows, fails = [], []
    auto = os.environ.get('TASK_IDX', '') == 'auto'
    out = OUT if not auto else \
        ROOT / 'bench' / os.environ.get('AUTO_OUT', 'drive_verified_auto.jsonl')
    try:
        if auto:
            call(session, 'open', AUTO_PAGES[0], '--headless')
            time.sleep(2)
            for page in AUTO_PAGES:
                tasks = auto_tasks(session, page)
                for task in tasks:
                    try:
                        r = capture(session, task)
                        rows.append(r)
                        with open(out, 'a', encoding='utf-8') as f:
                            f.write(json.dumps(r, ensure_ascii=False) + '\n')
                        print(f'ok   {task[1]}', flush=True)
                    except Exception as e:
                        fails.append((task[1], str(e)[:90]))
                        print(f'skip {task[1]}: {str(e)[:90]}', flush=True)
            call(session, 'close')
            print(f'captured {len(rows)} verified rows, {len(fails)} skipped -> {out}')
            return
        call(session, 'open', TASKS[0][0], '--headless')
        time.sleep(2)
        idx=set(int(x) for x in os.environ.get('TASK_IDX','').split(',') if x) or set(range(len(TASKS)))
        for i,task in enumerate(TASKS):
            if i not in idx: continue
            try:
                r=capture(session, task);rows.append(r)
                with open(OUT,'a',encoding='utf-8') as f:
                    f.write(json.dumps(r,ensure_ascii=False)+'\n')
                print(f'ok   {task[1]}', flush=True)
            except Exception as e:
                fails.append((task[1], str(e)[:90]))
                print(f'skip {task[1]}: {str(e)[:90]}', flush=True)
    finally:
        try:
            call(session, 'close')
        except Exception:
            print(f'could not close {session}', file=sys.stderr)

    print(f'captured {len(rows)} verified rows, {len(fails)} skipped -> {OUT}')


if __name__ == '__main__':
    main()
