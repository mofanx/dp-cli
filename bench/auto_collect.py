#!/usr/bin/env python3
"""真实站点自动采集——dpx 驱动真 Chrome：open→agent-state 拿渲染后
refs（绕过 MiniWoB 静态死路）→按角色模板生成 goal→chosen_ref=该元素。

用法: python3 bench/auto_collect.py [--sites sites.txt] 
输出: bench/collected_train.jsonl（append——与录制/静态采集同构）
"""
import json, subprocess, sys, time
from pathlib import Path

OUT = Path(__file__).parent / 'collected_train.jsonl'
SESSION = 'collect'
GOAL = {'link': '点击{name}', 'button': '点击{name}',
        'textbox': '在{name}输入', 'searchbox': '在{name}搜索',
        'combobox': '选择{name}', 'checkbox': '勾选{name}',
        'radio': '选择{name}', 'tab': '切到{name}'}

SITES = [
    'https://en.wikipedia.org',
    'https://en.wikipedia.org/wiki/Browser',
    'https://news.ycombinator.com',
    'https://github.com',
    'https://www.python.org',
    'https://developer.mozilla.org',
    'https://example.com',
    'https://www.npmjs.com',
    'https://duckduckgo.com',
]


def dpx(*args, timeout=45):
    r = subprocess.run(['dpx', *args, '--session', SESSION],
                       capture_output=True, text=True, timeout=timeout)
    try:
        # pretty JSON 多行输出——整体解析非尾行
        return json.loads(r.stdout)
    except Exception:
        return {'status': 'error', 'stdout': r.stdout[:200],
                'stderr': r.stderr[-200:]}


_FIRST = [True]


def collect_site(url):
    """开一页→agent-state→按元素产样本——单 headless 实例复用
    （--new 只首站用，每站各起 Chrome 会互抢 profile 锁）。"""
    args = ['--headless'] + (['--new'] if _FIRST[0] else [])
    r = dpx('open', url, *args)
    _FIRST[0] = False
    if r.get('status') != 'ok':
        print(f'  {url} open fail: {r.get("message", r.get("stderr","?"))[:60]}')
        return []
    time.sleep(1.2)                      # 等渲染稳定
    st = dpx('agent-state')
    opts = (st.get('data') or {}).get('options') or []
    refs = [{'ref': o['ref'], 'role': o['role'],
             'name': (o.get('name') or '')[:40]} for o in opts
            if o.get('name')]
    rows = []
    for o in refs:
        tmpl = GOAL.get(o['role'])
        if not tmpl:
            continue
        rows.append({'ts': int(time.time()), 'session': SESSION,
                     'src': 'auto-collect', 'page': url,
                     'goal': tmpl.format(name=o['name']),
                     'state': {'refs': refs}, 'chosen_ref': o['ref']})
    print(f'  {url}: {len(rows)} 样本（{len(refs)} 元素）')
    return rows


def main():
    sites = sys.argv[1:] or SITES
    # 真 Chrome attach（用户的浏览器，auto-connect）
    all_rows = []
    for u in sites:
        try:
            all_rows += collect_site(u)
        except subprocess.TimeoutExpired:
            print(f'  {u} timeout——跳过')
        except Exception as e:
            print(f'  {u} err: {e}')
    mode = 'a' if OUT.exists() else 'w'
    with OUT.open(mode, encoding='utf-8') as f:
        for r in all_rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(f'→ {OUT} +{len(all_rows)} 条')


if __name__ == '__main__':
    main()
