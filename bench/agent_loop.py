#!/usr/bin/env python3
"""agent 批量执行回路——goal 列表驱动 decide 多步回路攒 verified_real。

来源：Mind2Web confirmed_task（website→<site>.com 直链）/ WebVoyager goal JSONL。
护栏：只读类过滤（副作用关键词剔除）+站点风控 quarantine 复用 run_task_pool
      持久化 quarantine 表；每步 decide 落 verified_real（decide_train 自动采集
      前提是动作真执行——本 runner 执行 op 后 record 才产出执行后证据）。

用法：
  python bench/agent_loop.py --goals /tmp/agent_goals.jsonl --limit 10 \
      --max-steps 6 -S agentp --auto-connect
goal 行格式: {"id":"x","url":"https://...","goal":"..."}
"""
import argparse, json, os, sys, time

_CUR_SESSION=''
_CUR_GOAL=''
_CONNECT=['--auto-connect']
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_task_pool import run_dpx, _quar_load, QUAR_TTL  # noqa: E402

# 副作用关键词——命中即剔除（只读过滤护栏）
_SIDE_FX = ('purchase', 'buy ', 'checkout', 'add to cart', 'add-to-cart',
            'pay', 'order', 'book a', 'booking', 'reserve', 'post ',
            'submit', 'register', 'sign up', 'log in', 'login', 'sign in',
            'comment', 'upload', 'delete', 'write a', 'send ', 'bid', 'sell',
            'apply', 'follow ', 'subscribe', 'rent', 'cancel',
            '购买', '下单', '支付', '提交', '注册', '登录', '发帖', '评论',
            '上传', '删除', '发送', '预订', '预约', '关注', '取消')
_STEP_TIMEOUT = 60
_OPEN_DEAD_STREAK = 3          # 连续 open 超时此数→判浏览器死亡熔断
_CKPT_DIR = Path.home() / '.dp_cli' / 'agentloop_ckpt'


def _ckpt_file(session: str) -> Path:
    return _CKPT_DIR / f'{session}.json'


def _ckpt_save(session: str, done_ids: set, results: list) -> None:
    """进度持久化——批中断后 --resume 从断点续非重跑。"""
    _CKPT_DIR.mkdir(parents=True, exist_ok=True)
    (_CKPT_DIR / f'{session}.json').write_text(
        json.dumps({'done_ids': sorted(done_ids), 'results': results},
                   ensure_ascii=False))


def _ckpt_load(session: str) -> tuple:
    p = _ckpt_file(session)
    if not p.exists():
        return set(), []
    try:
        d = json.loads(p.read_text())
        return set(d.get('done_ids') or []), d.get('results') or []
    except Exception:
        return set(), []


def _chrome_alive(session: str) -> bool:
    """浏览器健康探针——tab-list 走完整 attach+daemon 链路，
    短超时失败即死亡（DevToolsActivePort 文件会 stale 残留，
    文件存在≠活着，必须走真链路探测）。"""
    r = run_dpx(['tab-list'], session, 15)
    return r['status'] == 'ok'


def _quarantined(site: str) -> int:
    """复用 run_task_pool 持久化 quarantine——命中返回剩余秒。"""
    q = _quar_load()
    t = q.get(site) or q.get((site or '').lower())
    if not t:
        return 0
    remain = int(QUAR_TTL - (time.time() - t))
    return remain if remain > 0 else 0


_VERIFIED_OUT = Path(__file__).parent / 'agent_verified.jsonl'


def _verified_delta(before: int) -> int:
    """扫本任务新增的 decide_train 证据行→转 verified_real 格式直写
    bench/drive_verified_auto.train.jsonl（eligible() 门禁认 src/page/证据）。
    daemon 录的行已带 pre/post_state+post_delta+chosen——真执行证据，
    此处只做格式对齐与 src 声明，不伪造。"""
    f = Path.home() / '.dp_cli' / 'sessions' / 'decide_train.jsonl'
    if not f.exists():
        return 0
    rows = [json.loads(l) for l in open(f, encoding='utf-8')]
    new = rows[before:] if len(rows) > before else []
    n = 0
    with _VERIFIED_OUT.open('a', encoding='utf-8') as fh:
        for r in new:
            if r.get('session') != _CUR_SESSION:
                continue
            row = {'src': 'verified_real', 'session': r['session'],
                   'ts': r.get('ts'), 'goal': _CUR_GOAL,
                   'page': (r.get('post_state') or {}).get('url')
                           or (r.get('pre_state') or {}).get('url'),
                   'state': r.get('state'), 'chosen': r.get('chosen'),
                   'pre_state': r.get('pre_state'),
                   'post_state': r.get('post_state'),
                   'post_delta': r.get('post_delta'),
                   'agent_loop': True}
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')
            n += 1
    return n


def _rows_now() -> int:
    f = Path.home() / '.dp_cli' / 'sessions' / 'decide_train.jsonl'
    return sum(1 for _ in open(f, encoding='utf-8')) if f.exists() else 0


def filtered_goals(path: str, limit: int) -> list:
    goals = [json.loads(l) for l in open(path, encoding='utf-8')
             if l.strip()]
    keep, drop = [], []
    for g in goals:
        low = (g.get('goal') or '').lower()
        if any(k in low for k in _SIDE_FX):
            drop.append(g['id'])
        else:
            keep.append(g)
    print(f'goals: {len(goals)} → 只读过滤后 {len(keep)}'
          f'（剔除 {len(drop)}: {drop[:6]}{"…" if len(drop)>6 else ""}）')
    return keep[:limit] if limit else keep


def _decide(session: str, goal: str) -> dict:
    r = run_dpx(['decide', goal], session, _STEP_TIMEOUT)
    d = r.get('data') or {}
    res = d.get('result') or d
    return {'status': r['status'], 'raw': r, 'action': res.get('action'),
            'ref': res.get('ref'), 'done': res.get('done'),
            'engine': d.get('engine'), 'conf': res.get('conf')}


def _exec_op(session: str, action: str, ref) -> dict:
    """执行 decide 出的 op——执行成功才落 verified_real 执行后证据。"""
    if action in ('click',) and ref:
        return run_dpx(['click', f'@e{ref}', '--no-wait'], session,
                       _STEP_TIMEOUT)
    if action in ('type', 'fill', 'type_text', 'select'):
        # 文本 op 无通用取值来源——返回 not_impl 让回路判 blocked
        return {'status': 'not_impl', 'code': 'SKIP_OP',
                'message': f'{action} 需要取值（回路当前只跑 click/scroll）'}
    if action in ('scroll_down',):
        return run_dpx(['eval', 'window.scrollBy(0,innerHeight*0.8)'],
                       session, 15)
    if action in ('scroll_up',):
        return run_dpx(['eval', 'window.scrollBy(0,-innerHeight*0.8)'],
                       session, 15)
    if action in ('back',):
        return run_dpx(['go-back'], session, 20)
    if action in ('wait',):
        time.sleep(1.5)
        return {'status': 'ok'}
    return {'status': 'not_impl', 'code': 'SKIP_OP',
            'message': f'未知 op {action}'}


def run_task(session: str, g: dict, max_steps: int) -> dict:
    global _CUR_SESSION,_CUR_GOAL
    _CUR_SESSION,_CUR_GOAL=session,g.get('goal','')
    site = (g.get('site') or '').lower()
    rem = _quarantined(site)
    if rem:
        return {'id': g['id'], 'verdict': 'quarantined', 'site': site,
                'quar_s': rem, 'steps': [], 'verified_delta': 0}
    before = _rows_now()
    rec = {'id': g['id'], 'site': site, 'verdict': 'partial', 'steps': [],
           'verified_delta': 0}
    r = run_dpx(['open', g['url'], *_CONNECT], session, 60)
    rec['steps'].append({'cmd': 'open', 'status': r['status']})
    if r['status'] != 'ok':
        rec['verdict'] = 'fail'
        return rec
    run_dpx(['wait', '--loaded', '--timeout', '20'], session, 30)
    # CF 质询页拦截——v2 难案实锤：x.com/cloudflare 类 interstitial
    # 上 decide 高置信连点"重新加载"死循环（3326 行同形态），
    # 入口即判 challenge 不烧步数
    _pu = run_dpx(['eval', 'location.href'], session, 15)
    _pu = (((_pu.get('data') or {}).get('result')) or '')
    if isinstance(_pu, str) and ('/cloudflare' in _pu
                                 or 'challenge' in _pu
                                 or '/cdn-cgi/' in _pu):
        rec['verdict'] = 'challenge_blocked'
        return rec
    stuck = 0
    reanchored = False
    for i in range(max_steps):
        d = _decide(session, g['goal'])
        act = d['action'] or 'blocked'
        rec['steps'].append({'i': i, 'op': act, 'ref': d.get('ref'),
                             'engine': d.get('engine'),
                             'conf': d.get('conf'),
                             'decide_status': d['status']})
        if act == 'blocked' and d['status'] == 'ok' and not reanchored:
            # decide 拒选多为 goal-页面错配（解剖实锤：中间步漂移后
            # 页面只剩营销导航/页脚，候选与 goal 无交集——模型拒选对）
            # ——回任务锚点重开一次再判死，区分"真无路"与"漂丢了"
            reanchored = True
            rec['steps'][-1]['reanchor'] = True
            run_dpx(['open', g['url'], *_CONNECT], session, 60)
            run_dpx(['wait', '--loaded', '--timeout', '20'], session, 30)
            continue
        if act == 'done' or (act == 'blocked' and d['status'] == 'ok'):
            rec['verdict'] = 'done' if act == 'done' else 'blocked'
            break
        if d['status'] != 'ok':
            stuck += 1
            if stuck >= 2:
                rec['verdict'] = 'decide_dead'
                break
            continue
        e = _exec_op(session, act, d.get('ref'))
        rec['steps'][-1]['exec'] = e['status']
        if e['status'] == 'not_impl':
            rec['verdict'] = 'op_unsupported'
            break
        if e['status'] != 'ok':
            stuck += 1
            if stuck >= 2:
                rec['verdict'] = 'exec_dead'
                break
            continue
        time.sleep(1.0)
    else:
        rec['verdict'] = 'max_steps'
    rec['verified_delta'] = _verified_delta(before)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--goals', required=True)
    ap.add_argument('--limit', type=int, default=10)
    ap.add_argument('--max-steps', type=int, default=6)
    ap.add_argument('-S', '--session', default='agentp')
    ap.add_argument('--auto-connect', action='store_true')
    ap.add_argument('--port', type=int, default=0,
                    help='显式调试端口（headless Chrome 场景，覆盖 --auto-connect）')
    ap.add_argument('--report', default='')
    ap.add_argument('--resume', action='store_true',
                    help='读 session 断点文件——跳过已完成任务续跑')
    a = ap.parse_args()
    goals = filtered_goals(a.goals, a.limit)
    done_ids, out = (_ckpt_load(a.session) if a.resume else (set(), []))
    if done_ids:
        goals = [g for g in goals if g['id'] not in done_ids]
        print(f'resume: 跳过已完成 {len(done_ids)}，续跑 {len(goals)}')
    global _CONNECT
    _CONNECT = ['--port', str(a.port)] if a.port else ['--auto-connect']
    if a.auto_connect or a.port:
        run_dpx(['daemon-start'], a.session, 30)
    t0 = time.time()
    open_dead = 0
    aborted = False
    for g in goals:
        if open_dead >= _OPEN_DEAD_STREAK:
            # Chrome 死亡级联熔断——tab-list 真链路探活复核后 break
            if not _chrome_alive(a.session):
                print(f'  ✗ 浏览器死亡熔断（{open_dead} 连 open 超时+探活败），'
                      '断点已存可 --resume', flush=True)
                aborted = True
                break
            open_dead = 0
        print(f"  ▶ {g['id']} [{g.get('site','')}] {g['goal'][:50]}",
              flush=True)
        r = run_task(a.session, g, a.max_steps)
        print(f"    {r['verdict']} steps={len(r['steps'])} "
              f"verified+{r['verified_delta']}", flush=True)
        out.append(r)
        done_ids.add(g['id'])
        _ckpt_save(a.session, done_ids, out)
        st0 = r['steps'][0] if r['steps'] else {}
        if r['verdict'] == 'fail' and st0.get('status') == 'timeout':
            open_dead += 1
        else:
            open_dead = 0
    vd = {'done': sum(1 for r in out if r['verdict'] == 'done'),
          'blocked': sum(1 for r in out if r['verdict'] == 'blocked'),
          'max_steps': sum(1 for r in out if r['verdict'] == 'max_steps'),
          'fail': sum(1 for r in out if r['verdict'] in
                      ('fail', 'decide_dead', 'exec_dead')),
          'unsupported': sum(1 for r in out
                           if r['verdict'] == 'op_unsupported'),
          'quarantined': sum(1 for r in out
                           if r['verdict'] == 'quarantined')}
    rep = {'goals': len(goals), 'verdicts': vd, 'aborted': aborted,
           'verified_delta_total': sum(r['verified_delta'] for r in out),
           'elapsed_s': round(time.time() - t0, 1), 'tasks': out}
    path = a.report or f'bench/reports/agentloop_{time.strftime("%Y%m%d_%H%M%S")}.json'
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print(f"\nverdicts: {vd}  verified_real+{rep['verified_delta_total']}  "
          f"{rep['elapsed_s']}s\nreport → {path}")


if __name__ == '__main__':
    main()
