#!/usr/bin/env python3
"""Record a local-only, outcome-checked pilot; never use it for training.

Run with TMPDIR on a writable disk if the host's /tmp has a user quota.
"""
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dp_cli.session import load_refs
from verified_data import has_outcome_evidence

OUT = ROOT / 'bench' / 'verified_pilot_local.jsonl'
TASKS = (
    (ROOT / 'tests/fixtures/hostile.html',
     '让 JS-Only Click 按钮触发一次点击事件', 'button', 'JS-Only Click',
     ('click',), 'window.__hit.click', 1),
    (ROOT / 'bench/fixtures/form.html',
     '在报名表的姓名框填入 Ada', 'textbox', 'Full name',
     ('fill', 'Ada'),
     'document.querySelector(\'input[placeholder="Full name"]\').value', 'Ada'),
    (ROOT / 'bench/fixtures/form.html',
     '勾选报名表的同意条款选项', 'checkbox', None,
     ('check',),
     'document.querySelector(\'input[type="checkbox"]\').checked', True),
)


def call(session, *args):
    cmd = ['dpx', *args, '--session', session]
    env = dict(os.environ, DPX_RECORD_DECIDE='0', DPX_NO_DAEMON='1')
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=45, env=env)
    try:
        response = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError(f'{args[0]} returned non-JSON (exit {proc.returncode})')
    if proc.returncode != 0 or response.get('status') != 'ok':
        raise RuntimeError(f'{args[0]} failed: {response.get("code", "UNKNOWN")}')
    return response


def snapshot(session):
    proc = subprocess.run(['dpx', 'snapshot', '--session', session],
                          capture_output=True, text=True, timeout=45,
                          env=dict(os.environ, DPX_RECORD_DECIDE='0',
                                   DPX_NO_DAEMON='1'))
    if proc.returncode != 0:
        raise RuntimeError(f'snapshot failed (exit {proc.returncode})')
    return load_refs(session)


def capture(session, task):
    page, goal, role, name, command, expr, expected = task
    call(session, 'goto', page.as_uri())
    refs = snapshot(session)
    options = [
        {'ref': int(k), 'role': v.get('role', ''),
         'name': v.get('name', '')}
        for k, v in refs.items() if str(k).isdigit()
    ]
    selected = [o for o in options
                if o['role'] == role and (name is None or o['name'] == name)]
    if len(selected) != 1:
        raise RuntimeError(f'{page.name}: expected one {role} {name!r}, '
                           f'got {len(selected)}')
    chosen = selected[0]
    ref = chosen['ref']
    before = call(session, 'eval', expr)
    argv = [command[0], f'ref:{ref}', *command[1:]]
    action = call(session, *argv)
    after = call(session, 'eval', expr)
    render = lambda o: f"{o['role']} {o['name']}".strip()
    row = {
        'src': 'pilot_local',
        'query': goal,
        'positive': render(chosen),
        'negatives': [render(o) for o in options if o['ref'] != ref],
        'page': page.as_uri(),
        'evidence': {
            'recorded_at': datetime.now(timezone.utc).isoformat(),
            'session': session,
            'url': page.as_uri(),
            'before_refs': options,
            'chosen_ref': ref,
            'before': {'expr': expr, 'result': before},
            'action': {'argv': argv, 'exit': 0,
                       'result': {'status': action['status']}},
            'after': {'expr': expr, 'expected': expected, 'result': after},
        },
    }
    if not has_outcome_evidence(row):
        raise RuntimeError(f'{page.name}: outcome proof failed for {command[0]}')
    return row


def main():
    if OUT.exists():
        raise SystemExit(f'refusing to overwrite {OUT}')
    session = 'verified_pilot_' + uuid.uuid4().hex[:10]
    rows = []
    try:
        call(session, 'open', TASKS[0][0].as_uri(), '--headless')
        for task in TASKS:
            rows.append(capture(session, task))
    finally:
        try:
            call(session, 'close')
        except (RuntimeError, subprocess.TimeoutExpired):
            print(f'could not close pilot session {session}', file=sys.stderr)
    OUT.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n'
                           for r in rows), encoding='utf-8')
    print(f'captured {len(rows)} outcome-checked local-only rows at {OUT}')


if __name__ == '__main__':
    main()
