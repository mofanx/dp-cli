#!/usr/bin/env python3
"""reranker vs external(纯 DOM 序 top1) decide A/B 实机对照（非主线代码）。

跑法：
  dpx open --headless → dpx daemon-start（模型驻留 daemon 进程）
  每场景：goto → agent-state(候选表/期望 ref 解析)
          → decide --engine external --answer '{target:1,...}' ×N
          → decide --engine reranker ×N
输出 bench/engine_ab_report.json。

external 基线协议：无模型——恒定答 target=1（DOM 序首候选），
action 由 ref1 的 role 经生产 _goal_action(goal, role) 映射（与
reranker 选中后同一映射，保证两引擎 action 推导口径一致）。
"""
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / 'bench' / 'fixtures'


def F(name):
    return f'file://{FIX / name}'


# expected: (名称子串, 期望动作前缀或 None)——name 子串在 agent-state
# options 里首个命中即期望 ref；meta 场景 expected=None 只判 action。
SCENARIOS = [
    # ── 简单 click ────────────────────────────────
    {'id': 'c1', 'cat': 'click', 'page': F('login.html'),
     'goal': '点击登录按钮',
     'expect': {'name': 'Sign In', 'role': 'button', 'action': 'click'}},
    {'id': 'c2', 'cat': 'click', 'page': F('buttons.html'),
     'goal': '点击导出按钮',
     'expect': {'name': '导出', 'role': 'button', 'action': 'click'}},
    {'id': 'c3', 'cat': 'click', 'page': F('dialog.html'),
     'goal': '取消这次删除操作',
     'expect': {'name': 'Cancel', 'role': 'button', 'action': 'click'}},
    # ── fill/type ────────────────────────────────
    {'id': 'f1', 'cat': 'type', 'page': F('login.html'),
     'goal': '填写用户名',
     'expect': {'name': 'Username', 'role': 'textbox', 'action': 'type'}},
    {'id': 'f2', 'cat': 'type', 'page': F('form.html'),
     'goal': '填写邮箱地址',
     'expect': {'name': 'Email', 'role': 'textbox', 'action': 'type'}},
    {'id': 'f3', 'cat': 'type', 'page': F('nav.html'),
     'goal': '搜索本站内容',
     'expect': {'name': 'Search', 'role': 'searchbox', 'action': 'type'}},
    # ── 歧义场（权重放大 6 条）─────────────────────
    {'id': 'a1', 'cat': 'ambig', 'page': F('ambig_auth.html'),
     'goal': '注册新账号',
     'expect': {'name': '注册新账号', 'role': 'button', 'action': 'click'},
     'note': '登录语义紧邻干扰：导航登录链接+表单登录按钮'},
    {'id': 'a2', 'cat': 'ambig', 'page': F('ambig_auth.html'),
     'goal': '点登录',
     'expect': {'name': '登录', 'role': 'button', 'action': 'click'},
     'note': '同名 link vs submit button——期望主操作 button'},
    {'id': 'a3', 'cat': 'ambig', 'page': F('ambig_item.html'),
     'goal': '删除这条记录',
     'expect': {'name': '删除', 'role': 'button', 'action': 'click'},
     'note': '删除 vs 归档 近义；两行重复控件'},
    {'id': 'a4', 'cat': 'ambig', 'page': F('ambig_item.html'),
     'goal': '归档这条记录',
     'expect': {'name': '归档', 'role': 'button', 'action': 'click'},
     'note': 'a3 反向——归档是第二个候选组'},
    {'id': 'a5', 'cat': 'ambig', 'page': F('ambig_dup.html'),
     'goal': '查看耳机的详情',
     'expect': {'name': '查看耳机详情', 'role': 'button',
                'action': 'click'},
     'note': '三个同名"详情"按钮，aria-label 区分上下文'},
    {'id': 'a6', 'cat': 'ambig', 'page': F('product.html'),
     'goal': '立即购买下单',
     'expect': {'name': 'Buy now', 'role': 'button', 'action': 'click'},
     'note': 'Buy now vs Add to cart 近义干扰'},
    {'id': 'a7', 'cat': 'ambig', 'page': F('ambig_check.html'),
     'goal': '开启深色模式',
     'expect': {'name': 'Dark mode', 'role': 'checkbox',
                'action': 'click'},
     'note': '相邻 checkbox Enable notifications 干扰'},
    # ── meta 短路 ────────────────────────────────
    {'id': 'm1', 'cat': 'meta', 'page': F('buttons.html'),
     'goal': '返回上一页', 'expect': {'action': 'back'}},
    {'id': 'm2', 'cat': 'meta', 'page': F('buttons.html'),
     'goal': '向下滚动查看更多内容',
     'expect': {'action': 'scroll_down'}},
    {'id': 'm3', 'cat': 'meta', 'page': F('done.html'),
     'goal': '确认提交已成功', 'expect': {'action': 'done'}},
    {'id': 'm4', 'cat': 'meta', 'page': F('blocked.html'),
     'goal': '继续下一步操作', 'expect': {'action': 'blocked'}},
    {'id': 'm5', 'cat': 'meta', 'page': F('checkout.html'),
     'goal': '返回购物车',
     'expect': {'action': 'back'},
     'note': 'meta 与语义冲突用例：goal 指 Back-to-cart 链接，'
             'meta 规则按"返回"误短路为浏览器 back'},
    # ── 真实站（github.com，97 候选→reranker 走 embed 召回）──
    {'id': 'g1', 'cat': 'ambig', 'page': 'https://github.com',
     'goal': '点击 Sign in 登录',
     'expect': {'name': 'Sign in', 'role': 'link', 'action': 'click'},
     'note': '真实站：Sign in link vs Sign up button 近义对'},
    {'id': 'g2', 'cat': 'ambig', 'page': 'https://github.com',
     'goal': '注册 GitHub 账号',
     'expect': {'name': 'Sign up', 'role': 'button',
                'action': 'click'},
     'note': 'g1 反向——注册歧义对'},
]

RUNS_EXT = 2
RUNS_RER = 3          # 热载采样次数（冷载首跑单列）
# margin 阈值扫档：DP_AB_MIN_MARGIN=0.05 复跑对照（默认=CLI 现状值）
MIN_MARGIN = os.environ.get('DP_AB_MIN_MARGIN')


def dpx(*args, timeout=120):
    """跑一条 dpx 命令 → (wall_ms, parsed_json_or_None, raw)。"""
    t0 = time.perf_counter()
    p = subprocess.run(['dpx', *args], capture_output=True,
                       text=True, timeout=timeout)
    ms = (time.perf_counter() - t0) * 1000
    out = (p.stdout or '').strip()
    data = None
    try:                                   # pretty 多行整体优先
        data = json.loads(out)
    except json.JSONDecodeError:
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith('{'):
                try:
                    data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
    return ms, data, p


def goal_action(goal, role):
    sys.path.insert(0, str(ROOT))
    from dp_cli.agent import _goal_action
    return _goal_action(goal, role)


def run_decide(goal, engine, answer=None):
    args = ['decide', goal, '--engine', engine]
    if answer is not None:
        args += ['--answer', answer]
    if MIN_MARGIN is not None:
        args += ['--min-margin', MIN_MARGIN]
    ms, data, p = dpx(*args)
    row = {'ms': round(ms, 1)}
    if data is None:
        row['parse_error'] = (p.stdout or '')[-200:] + \
            (p.stderr or '')[-200:]
        return row
    d = data.get('data') or {}
    row['status'] = data.get('status')
    row['code'] = data.get('code')
    if data.get('status') == 'error' and data.get('code') == 'AGENT_UNSURE':
        try:
            d = json.loads(data.get('detail') or '{}')
        except json.JSONDecodeError:
            d = {}
        row['unsure'] = True
    row['engine'] = d.get('engine')
    row['action'] = d.get('action')
    row['ref'] = d.get('ref')
    row['conf'] = d.get('confidence')
    row['margin'] = d.get('margin')
    row['tier'] = d.get('tier')
    row['reason'] = d.get('reason')
    row['deterministic'] = d.get('deterministic')
    pt = (d.get('probs') or {}).get('target') or {}
    row['top3'] = sorted(pt.items(), key=lambda x: -x[1])[:3]
    return row


def main():
    report = {'meta': {}, 'scenarios': []}

    # ── 环境准备 ─────────────────────────────────
    dpx('close')                                  # 清场
    dpx('daemon-stop')                            # 防残留 sock 误路由
    time.sleep(0.5)
    ms, data, p = dpx('open', '--headless')
    report['meta']['open_ms'] = round(ms, 1)
    if not data or data.get('status') != 'ok':
        print('open 失败:', data,
              '\nOUT:', (p.stdout or '')[-500:],
              '\nERR:', (p.stderr or '')[-500:])
        sys.exit(1)
    ms, data, _ = dpx('daemon-start')
    report['meta']['daemon_start_ms'] = round(ms, 1)
    print('daemon:', json.dumps(data, ensure_ascii=False)[:120])

    cold_logged = False

    try:
        for sc in SCENARIOS:
            rec = {'id': sc['id'], 'cat': sc['cat'], 'goal': sc['goal'],
                   'page': sc['page'].split('/')[-1],
                   'expect': sc['expect']}
            if sc.get('note'):
                rec['note'] = sc['note']

            ms, _, _ = dpx('goto', sc['page'])
            rec['goto_ms'] = round(ms, 1)
            time.sleep(0.3)

            # 候选表 → 期望 ref 解析
            _, st, _ = dpx('agent-state')
            opts = ((st or {}).get('data') or {}).get('options') or []
            rec['options_count'] = len(opts)
            rec['options'] = [{'ref': o['ref'], 'role': o['role'],
                               'name': o['name']} for o in opts]
            exp_ref = None
            if 'name' in sc['expect']:
                for o in opts:
                    if sc['expect']['name'].lower() in \
                            (o.get('name') or '').lower() and (
                            not sc['expect'].get('role')
                            or o['role'] == sc['expect']['role']):
                        exp_ref = o['ref']
                        break
            rec['expect_ref'] = exp_ref

            # ── external：恒定 target=1（DOM 序首候选）──
            act1 = goal_action(sc['goal'],
                               opts[0]['role'] if opts else '')
            answer = json.dumps(
                {'action': act1, 'target': 1, 'done': False,
                 'stuck': False, 'confidence': 0.99,
                 'probs': {'target': {'1': 0.99, '2': 0.01}}},
                ensure_ascii=False)
            ext_runs = [run_decide(sc['goal'], 'external', answer)
                        for _ in range(RUNS_EXT)]
            rec['external'] = ext_runs

            # ── reranker：首跑冷载单列，之后热载 ×N ──
            if not cold_logged:
                cold = run_decide(sc['goal'], 'reranker')
                rec['reranker_cold'] = cold
                cold_logged = True
            else:
                # 保持样本结构——每场景仍做冷后首跑
                pass
            rer_runs = [run_decide(sc['goal'], 'reranker')
                        for _ in range(RUNS_RER)]
            rec['reranker'] = rer_runs

            # 判定：meta 场景比 action；elem 场景比 ref
            def judge(r):
                if r.get('engine') == 'meta-rules' or \
                        sc['cat'] == 'meta':
                    return r.get('action') == sc['expect'].get('action')
                return r.get('ref') == exp_ref and exp_ref is not None

            rec['ext_top1_ok'] = judge(ext_runs[0])
            rec['rer_top1_ok'] = judge(
                rec.get('reranker_cold') or rer_runs[0])
            rec['meta_hit'] = (ext_runs[0].get('engine') == 'meta-rules')
            rec['meta_reason'] = ext_runs[0].get('reason')
            rec['unsure_ext'] = any(r.get('unsure') for r in ext_runs)
            rec['unsure_rer'] = any(r.get('unsure') for r in rer_runs)
            report['scenarios'].append(rec)
            print(f"{sc['id']:>3} {sc['goal'][:18]:<20} "
                  f"ext={ext_runs[0].get('ref')}({ext_runs[0].get('action')}) "
                  f"rer={(rec.get('reranker_cold') or rer_runs[0]).get('ref')}"
                  f"({(rec.get('reranker_cold') or rer_runs[0]).get('action')}) "
                  f"exp={exp_ref or sc['expect'].get('action')} "
                  f"meta={rec['meta_hit']}")
    finally:
        dpx('daemon-stop')
        dpx('close')

    Path(ROOT / 'bench' / 'engine_ab_report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding='utf-8')
    print('\nwrote bench/engine_ab_report.json')


if __name__ == '__main__':
    main()
