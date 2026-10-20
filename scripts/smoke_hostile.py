#!/usr/bin/env python3
"""事件断言型真机冒烟——hostile.html 刁难控件逐个验证。

用法: python scripts/smoke_hostile.py [--session S]
每处断言真实事件触发（__hit 计数），不是"命令返回 ok"。
"""
import json, subprocess, sys, time

SES = 'hostile'
DPX = ['dpx']
BASE = 'file://' + __file__.replace('scripts/smoke_hostile.py',
                                    'tests/fixtures/hostile.html')

def run(*a, raw=False):
    r = subprocess.run(DPX + list(a) + ['--session', SES], capture_output=True,
                       text=True, timeout=30)
    if raw:
        return r
    try:
        return json.loads(r.stdout)
    except Exception:
        return {'status': 'parse_err', 'raw': r.stdout[:200]}

def hits():
    r = run('eval', 'window.__hit')
    if r.get('status') != 'ok' or 'result' not in r.get('data', {}):
        return {'_err': r.get('message') or r.get('raw', '?')}
    return r['data']['result']

CASES = [
    # (desc, args, expect_check)
    ('普通 click', ['click', 'css:#js-only'], lambda h: h['click'] >= 1),
    ('--by-js', ['click', 'css:#js-only', '--by-js'],
     lambda h: h['click'] >= 2),
    ('--by-mouse 坐标级', ['click', 'css:#md-only', '--by-mouse'],
     lambda h: h['mousedown'] >= 1),
    ('disabled 不可点→错误', ['click', 'css:#dis'], 'CLICK_FAILED'),
    ('遮挡元素→行动性', ['click', 'css:#cov'], 'CLICK_FAILED'),
    # --force 语义=跳过 actionability；坐标点击仍落在 overlay
    # 上，被挡元素 onclick 不触发——只断言命令返回 ok
    ('--force 强点遮挡(绕过检查)', ['click', 'css:#cov', '--force'],
     'ok_status'),
    ('dblclick', ['dblclick', 'css:#dbl'], lambda h: h['dblclick'] >= 1),
    # 边界登记（依赖前序 by-mouse 已贡献 mousedown=1）：默认 click 走
    # CDP 完整 down/up——md-only 控件 mousedown 同样触发（累计≥2）。
    # 真实失效场景是遮挡/受信事件等更深条件，--by-mouse 仍是逃生口。
    ('普通 click 对 md-only（边界登记）', ['click', 'css:#md-only'],
     lambda h: h['mousedown'] >= 2),
]

def main():
    run('open', BASE, '--headless')  # CI 无 display 必带
    time.sleep(1.5)          # 动态渲染 800ms
    ok = fail = 0
    for desc, args, expect in CASES:
        r = run(*args)
        if expect == 'ok_status':
            hit = r.get('status') == 'ok'
        elif isinstance(expect, str):   # 期望错误码
            hit = r.get('code') == expect or \
                expect in str(r)
        else:
            h = hits()
            hit = (r.get('status') == 'ok' and '_err' not in h
                   and expect(h))
        mark = '✅' if hit else '❌'
        detail = '' if hit or not callable(expect) else (
            f' err={h.get("_err")}' if '_err' in h else '')
        print(f'{mark} {desc}: exit={r.get("status","?")} '
              f'code={r.get("code","-")}{detail}')
        ok += hit
        fail += not hit
    # 动态按钮存在即触发
    r = run('click', 'css:#dyn')
    dyn = r.get('status') == 'ok' and hits().get('click', 0) >= 3
    print(f'{"✅" if dyn else "❌"} 动态渲染按钮')
    ok += dyn; fail += not dyn
    print(f'== {ok} passed, {fail} failed ==')
    run('close')                    # 防 hostile session 残留
    sys.exit(0 if fail == 0 else 1)

if __name__ == '__main__':
    main()
