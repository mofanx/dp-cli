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

OUT = ROOT / 'bench' / 'drive_verified_d4.jsonl'

TI = 'https://the-internet.herokuapp.com'
BT = 'https://books.toscrape.com'
DQ = 'https://demoqa.com'

# (goto_url|None=stay, goal, role, name|None, cmd_argv, eval_expr, expected,
#  wait_seconds, pick_index)
TASKS = [
    # books.toscrape——分类/翻页列表浏览（与 d3 不同分类=自然多样）
    (BT + '/', '进入 Fantasy 分类', 'link', 'Fantasy',
     ('click',), 'location.pathname', '/catalogue/category/books/fantasy_19/index.html', 0),
    (BT + '/', '查看 Mystery 分类书籍', 'link', 'Mystery',
     ('click',), 'location.pathname', '/catalogue/category/books/mystery_3/index.html', 0),
    (BT + '/', '进入 Poetry 分类', 'link', 'Poetry',
     ('click',), 'location.pathname', '/catalogue/category/books/poetry_23/index.html', 0),
    (BT + '/catalogue/page-2.html', '翻到第三页列表', 'link', 'next',
     ('click',), 'location.pathname', '/catalogue/page-3.html', 0),
    (BT + '/catalogue/category/books/travel_2/index.html', '打开 Travel 分类第一本书',
     'link', None,
     ('click',), 'location.pathname',
     '/catalogue/its-only-the-himalayas_981/index.html', 0, 0),
    # wikipedia——条目内导航（Talk/历史/源码视图）
    ('https://en.wikipedia.org/wiki/Python_(programming_language)',
     '查看 Python 条目讨论页', 'link', 'Talk',
     ('click',), 'location.pathname', '/wiki/Talk:Python_(programming_language)', 0),
    ('https://en.wikipedia.org/wiki/Python_(programming_language)',
     '查看 Python 条目编辑历史', 'link', 'View history',
     ('click',), 'location.search', '?title=Python_(programming_language)&action=history', 0),
    ('https://en.wikipedia.org/wiki/Browser_engine',
     '查看排版引擎条目源码', 'link', 'View source',
     ('click',), 'location.search', '?title=Browser_engine&action=edit', 0),
    # pypi——包索引内容页
    ('https://pypi.org/', '打开 PyPI 帮助页', 'link', 'PyPI Help',
     ('click',), 'location.pathname', '/help/', 0),
    ('https://pypi.org/search/?q=browser', '点开第一个搜索结果的包',
     'link', None, ('click',), 'location.pathname', '/project/playwright-stealth/', 2, 0),
    # HN——列表条目导航
    ('https://news.ycombinator.com', '打开 HN 评论导航', 'link', 'comments',
     ('click',), 'location.pathname', '/newcomments', 0),
    ('https://news.ycombinator.com', '打开 Ask 列表', 'link', 'ask',
     ('click',), 'location.pathname', '/ask', 0),
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
    session = 'verified_d4_' + uuid.uuid4().hex[:10]
    rows, fails = [], []
    try:
        call(session, 'open', TASKS[0][0], '--port', '21122')
        time.sleep(2)
        idx=set(int(x) for x in os.environ.get('TASK_IDX','').split(',') if x) or set(range(len(TASKS)))
        for i,task in enumerate(TASKS):
            if i not in idx: continue
            try:
                rows.append(capture(session, task))
                print(f'ok   {task[1]}', flush=True)
            except Exception as e:
                fails.append((task[1], str(e)[:90]))
                print(f'skip {task[1]}: {str(e)[:90]}', flush=True)
    finally:
        try:
            call(session, 'close')
        except Exception:
            print(f'could not close {session}', file=sys.stderr)
    mode = 'a' if OUT.exists() else 'w'
    with open(OUT, mode, encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(f'captured {len(rows)} verified rows, {len(fails)} skipped -> {OUT}')


if __name__ == '__main__':
    main()
