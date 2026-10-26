"""verified_real 采集（d2·表单/输入类）——公开演示站，断言不闭合如实跳过。

契约对齐 bench/verified_data.py eligible()：dpx eval 返回
{data:{result:X}}，故 evidence.before/after 存整响应对象。
增量模式：TASK 表驱动，追加写 jsonl。
"""
import json, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, 'bench')
from verified_data import eligible

OUT = Path('bench/drive_verified_d2.jsonl')

def dpx(*a):
    r = subprocess.run(['dpx', *a], capture_output=True, text=True, timeout=40)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {'status': 'err'}

def shot(expr):
    return {'expr': expr, 'result': dpx('eval', expr)}

def refs_now():
    st = dpx('agent-state')
    # name 可空（combobox 等 role-only 元素）——用 current/role 兜底
    return [{'ref': o['ref'], 'role': o['role'],
             'name': o.get('name') or o.get('current') or o['role']}
            for o in (st.get('data') or {}).get('options') or []]

def attempt(goal, argv_cmd, ref, refs, page, expr, expected, rows):
    b = shot(expr)
    old = (b['result'].get('data') or {}).get('result')
    if not isinstance(old, type(expected)) or old == expected:
        print('SKIP', goal, f'before={old!r}'); return
    ex = dpx(*argv_cmd)
    if ex.get('status') != 'ok':
        print('SKIP', goal, 'exec fail'); return
    time.sleep(.5)
    a = shot(expr); a['expected'] = expected
    new = (a['result'].get('data') or {}).get('result')
    pos = next((f"{o['role']} {o['name']}" for o in refs if o['ref'] == ref), '')
    row = {'src': 'verified_real', 'page': page, 'goal': goal, 'argv': argv_cmd,
           'query': goal, 'positive': pos,
           'negatives': [f"{o['role']} {o['name']}" for o in refs if o['ref'] != ref],
           'evidence': {'url': page, 'before_refs': refs, 'chosen_ref': ref,
                        'action': {'argv': argv_cmd, 'exit': 0, 'result': ex},
                        'before': b, 'after': a}}
    ok = eligible(row)
    if ok:
        rows.append(row)
    print(('PASS' if ok else 'FAIL'), goal, f'{old!r}→{new!r}')

# (page, goal, pick_role/索引, argv模板, expr, expected)
TASKS = [
    ('https://the-internet.herokuapp.com/inputs', '在数字输入框填入42',
     ('role', 'spin'), ['fill', '{ref}', '42'],
     "document.querySelector('input[type=number]').value", '42'),
    ('https://the-internet.herokuapp.com/checkboxes', '勾选第1个复选框',
     ('checkbox', 0), ['check', '{ref}'],
     "document.querySelectorAll('input[type=checkbox]')[0].checked", True),
    ('https://the-internet.herokuapp.com/login', '在用户名框填入tomsmith',
     ('role', 'textbox', 0), ['fill', '{ref}', 'tomsmith'],
     "document.querySelector('#username').value", 'tomsmith'),
    ('https://the-internet.herokuapp.com/login', '在密码框填入Secret',
     ('role', 'textbox', 1), ['fill', '{ref}', 'Secret!'],
     "document.querySelector('#password').value", 'Secret!'),
    ('https://the-internet.herokuapp.com/add_remove_elements/',
     '点击Add Element按钮', ('role', 'button', 0), ['click', '{ref}'],
     "!!document.querySelector('.added-manually')", True),
    ('https://the-internet.herokuapp.com/dynamic_controls', '点击Enable启用输入框',
     ('role', 'button', 1), ['click', '{ref}'],
     "!document.querySelector('#input-example input').disabled", True),
    # deep_semantic A 组——goal 与元素 name 无字面重叠（miss n05/n24/n27 类）
    # localhost fixture 网络栈不通改真站——the-internet 登录页 name=Username/Password
    ('https://the-internet.herokuapp.com/login', '填登录名',
     ('role', 'textbox', 0), ['fill', '{ref}', 'user_42'],
     "document.querySelector('#username').value", 'user_42'),
    ('https://the-internet.herokuapp.com/login', '输入口令',
     ('role', 'textbox', 1), ['fill', '{ref}', 'hunter2'],
     "document.querySelector('#password').value", 'hunter2'),
    ('https://the-internet.herokuapp.com/forgot_password', '留电邮要重置',
     ('role', 'textbox', 0), ['fill', '{ref}', 'a@b.co'],
     "document.querySelector('#email').value", 'a@b.co'),
]

def pick(refs, kind):
    mode, val, *rest = kind
    if mode == 'checkbox':
        l = [o for o in refs if 'checkbox' in o['role']]
        return l[val] if len(l) > val else None
    l = [o for o in refs if val in o['role']]
    idx = rest[0] if rest else 0
    return l[idx] if len(l) > idx else None

def main():
    dpx('open', '--auto-connect')
    rows = []
    if OUT.exists():
        try:
            rows = json.loads(OUT.read_text(encoding='utf-8'))
        except Exception:
            rows = []
    for page, goal, kind, argv_t, expr, expected in TASKS:
        dpx('goto', page); time.sleep(1.5)
        refs = refs_now()
        o = pick(refs, kind)
        if not o:
            print('SKIP', goal, 'no elem'); continue
        argv = [a.format(ref=f"@e{o['ref']}") for a in argv_t]
        attempt(goal, argv, o['ref'], refs, page, expr, expected, rows)
    json.dump(rows, open(OUT, 'w'), ensure_ascii=False, indent=1)
    print('VERIFIED total:', len(rows))

if __name__ == '__main__':
    main()
