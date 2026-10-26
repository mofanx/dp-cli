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
    url = eval_raw(session, 'location.href')['data']['result']
    before = call(session, 'eval', expr)
    old = before['data']['result']
    if old == expected:
        raise RuntimeError(f'pre-state already satisfies outcome ({old!r})')
    argv = [command[0], f'ref:{ref}', *command[1:]]
    action = call(session, *argv)
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


def main():
    session = 'verified_d3_' + uuid.uuid4().hex[:10]
    rows, fails = [], []
    try:
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
