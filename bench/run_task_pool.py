#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""任务池跑批器——"做任务攒数据"：逐条回放 jsonl 任务，攒 netlog/decide 实测数据。

输入  bench/tasks/site_tasks.jsonl   schema:
  {task_id, site, goal,
   steps: [{cmd, args?, opts?, timeout?, optional?, expect?} | {"nl": 自然语言} | "自然语言"],
   expect: {kind, ...}}

step.cmd → dpx argv 直组（args 为位置参数，opts 为旗表）：
  open/eval/fill/click/decide/sleep/api/snapshot/press/wait/tab-select/autoscroll...
  {"cmd":"assert"} 即时判定（step.expect 优先，缺省回落 task.expect）
  首个 open 步自动注入 --auto-connect --browser <path>（--no-auto-connect 关）

expect.kind:
  json_field      {path, eq|ne|contains|gt|lt|truthy, step?}  对指定步 JSON 取虚线路径
  eval_contains   {expr?=document.body.innerText, needle|any_of|all_of}
  url_contains    {needle|any_of|all_of}
  stdout_contains {needle|any_of|all_of, step?}
  step_code       {step, needle|any_of|all_of}                指定步 status/code/message 命中
  status_ok       {}                                          全部步 status==ok

任务级聚合:
  {task_id, verdict pass/fail/partial, total_ms, steps_n,
   decide_calls:[{engine,conf,tier,action,escalate}],
   bugs:[现象关键词], netlog_delta:~/.dp_cli/netlog-<session>.jsonl 前后行差}

输出  bench/reports/taskpool_<ts>.json + 终端表格式摘要。

用法:
  python bench/run_task_pool.py                          # 全量
  python bench/run_task_pool.py --only jd_item_detail,mmb_history_query
  python bench/run_task_pool.py --session taskpool --timeout 120
"""
from __future__ import annotations

import argparse
import os
import json
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TASKS = ROOT / 'bench' / 'tasks' / 'site_tasks.jsonl'
REPORT_DIR = ROOT / 'bench' / 'reports'

# 失败步 stdout/code/message 里扫这些现象词 → bugs 分类计数
BUG_KEYWORDS = [
    'occluded', 'UNSERE', 'UNSURE', 'AGENT_UNSURE', 'JS_FAILED',
    'ELEMENT_NOT_FOUND', 'REF_NOT_FOUND', 'REF_UNRESOLVABLE', 'NO_REFS',
    'TIMEOUT', 'WAIT_TIMEOUT', 'WAIT_FAILED', 'TAB_FAILED',
    'SESSION_NOT_FOUND', 'SESSION_DISCONNECTED',
    'NAVIGATE_FAILED', 'CLICK_FAILED', 'FILL_FAILED', 'TYPE_FAILED',
    'AUTOCONNECT_FAILED', 'BROWSER_START_FAILED', 'API_FETCH_FAILED',
    'HTTP_FAILED', 'DECIDE_FAILED', 'ASSERT_FAILED', 'NON_JSON',
    'NONZERO_EXIT', 'captcha', '验证码', 'blocked', 'NET_DOWN',
]
BUG_RE = re.compile('|'.join(re.escape(k) for k in BUG_KEYWORDS), re.IGNORECASE)

# 风控/验证页特征——命中则任务降级 partial+RISK_CONTROL 归因
# （JD 凌晨风控实测：验证页 DOM 无目标元素，ELEMENT_NOT_FOUND
# 会被误记为产品 fail；URL/title 双源判定）
RISK_RE = re.compile(
    r'risk_handler|/privatedomain/|京东验证|验证中心|sec-captcha|'
    r'__jsl_clearance|punish\?|captcha[-_]verify|waf_|slide[-_]verify|'
    # CF 质询 interstitial——x.com/cloudflare 类"重新加载"死循环
    # 1485 行实锤；/cloudflare|/cdn-cgi 按路径段匹配（cloudflare.com
    # 域名本身是合法任务目标不误伤）+标题 Just a moment 兜底
    r'/(cloudflare|cdn-cgi)(/|$)|just a moment|verify you are human',
    re.IGNORECASE)
NAV_CMDS = {'open', 'goto', 'tab-new', 'press'}

# 风控避让：命中风控的站点进 quarantine（TTL 内同站任务整批跳过，
# 不再硬撞加深风控画像——同站每轮硬撞越撞越黑实测归因）；
# 落盘持久化跨批生效。DPX_RISK_QUAR_TTL=0 关隔离；
# DPX_RISK_RETRY_DELAY=0 关命中后重试（滑块常一次自过）。
QUAR_FILE = Path.home() / '.dp_cli' / 'risk_quarantine.json'
QUAR_TTL = int(os.environ.get('DPX_RISK_QUAR_TTL', '7200'))
RISK_RETRY_DELAY = float(os.environ.get('DPX_RISK_RETRY_DELAY', '45'))


def _quar_load() -> dict:
    """过期的隔离项自动剔除。"""
    try:
        d = json.loads(QUAR_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}
    now = time.time()
    return {s: t for s, t in d.items() if now - t < QUAR_TTL}


def _quar_mark(site: str) -> None:
    if not site or QUAR_TTL <= 0:
        return
    q = _quar_load()
    q[site] = time.time()
    try:
        QUAR_FILE.parent.mkdir(parents=True, exist_ok=True)
        QUAR_FILE.write_text(json.dumps(q), encoding='utf-8')
    except OSError:
        pass


STDOUT_TAIL = 500        # 每步 stdout 摘要截断
DPX = 'dpx'

# 无 -S/--session 旗的命令——统一补 -S 会 INVALID_ARGS（mmb 真机归因：
# dpx sleep 不接 session 旗，批跑 sleep 步全灭——sleep 已补 -S 空操作旗）
SESSIONLESS = {'kill-all', 'close-all', 'list', 'catalog',
               'install', 'model', 'model-status'}


# ── dpx 调用 ──────────────────────────────────────────────────────────────

def _opts_argv(opts: dict) -> list:
    """opts dict → CLI 旗表：True→--flag；False/None 跳过；其余→--flag v。"""
    argv = []
    for k, v in (opts or {}).items():
        if v is None or v is False:
            continue
        flag = '--' + k.replace('_', '-')
        if v is True:
            argv.append(flag)
        else:
            argv += [flag, str(v)]
    return argv


def step_calls(step: dict, cfg) -> list:
    """step dict → dpx argv 列表（多数步 1 条；open 在 auto-connect
    共享 Chrome 下翻译为 attach+pin/navigate 子调用组——见下行注释）。

    open 翻译（实测归因：auto-connect 下 `dpx open <url>` 曾走
    ChromiumPage.get=tab_ids[0] 劫持用户首个标签页——dpx 侧已修：
    open --auto-connect 无 active_tab 绑定时自带 new_tab+绑定，
    故首 open 直传 URL 一条命令完成 attach+专属 tab 导航；且跨进程
    _cur_tab 无 active_tab 绑定时 latest_tab 随焦点漂移，wait/fill
    打到无关 tab 报 ELEMENT_NOT_FOUND——首跑 3/4 fail 归因）：
      未 attach:  open --auto-connect --browser B <url>
                  （attach + 自动建专属 tab 并绑 active_tab）
      未 pin:     tab-new <url>
      已 pin:     goto <url>                        (在绑定 tab 内导航)
    --no-auto-connect（独立浏览器）下 open 原样透传。
    """
    cmd = step['cmd']
    args = [str(a) for a in step.get('args') or []]
    extra = _opts_argv(step.get('opts'))

    if cmd == 'open' and cfg.auto_connect:
        url = args[0] if args else None
        if not cfg.attached:
            cfg.attached = True
            cfg.pinned = bool(url)
            return [['open', '--auto-connect', '--browser', cfg.browser,
                     *([url] if url else []), *extra]]
        if not cfg.pinned:
            cfg.pinned = bool(url)
            return [['tab-new', url]] if url else []
        return [['goto', url, *extra]] if url else []

    return [[cmd, *args, *extra]]


def run_dpx(argv: list, session: str, timeout: int) -> dict:
    """子进程跑 dpx，返回归一化记录 {status,code,message,data,stdout,elapsed_ms}。"""
    full = ([DPX, *argv] if argv and argv[0] in SESSIONLESS
            else [DPX, *argv, '-S', session])
    t0 = time.monotonic()
    try:
        proc = subprocess.run(full, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'status': 'timeout', 'code': 'TIMEOUT', 'data': None,
                'message': f'step 超时 {timeout}s',
                'stdout': '', 'elapsed_ms': int((time.monotonic() - t0) * 1000),
                'exit': -1}
    ms = int((time.monotonic() - t0) * 1000)
    out = (proc.stdout or '').strip()
    parsed = None
    try:
        parsed = json.loads(out)
    except ValueError:
        # JSONL 多行输出（batch/serve 类命令逐条 JSON）——取末行
        # summary/结果 JSON 兜底，整串 parse 必挂
        for ln in reversed(out.splitlines()):
            ln = ln.strip()
            if ln.startswith('{'):
                try:
                    parsed = json.loads(ln)
                    break
                except ValueError:
                    continue
        if parsed is None:
            m = re.search(r'\{.*\}', out, re.DOTALL)   # stdout 混入非 JSON 前缀时兜底
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except ValueError:
                    parsed = None
    if isinstance(parsed, dict) and 'status' not in parsed \
            and 'summary' in parsed:
        parsed = {'status': 'ok', 'data': parsed}
    if parsed is None:
        return {'status': 'error', 'code': 'NON_JSON', 'data': None,
                'message': f'非 JSON 输出(exit {proc.returncode})',
                'stdout': out[-STDOUT_TAIL:], 'elapsed_ms': ms,
                'exit': proc.returncode}
    status = parsed.get('status', 'error')
    return {'status': 'ok' if status == 'ok' and proc.returncode == 0 else 'error',
            'code': parsed.get('code') or ('' if status == 'ok' else 'NONZERO_EXIT'),
            'message': parsed.get('message') or '',
            'detail': parsed.get('detail') or '',
            'data': parsed.get('data'),
            'stdout': out[-STDOUT_TAIL:],
            'elapsed_ms': ms, 'exit': proc.returncode}


# ── expect 判定 ────────────────────────────────────────────────────────────

def _dig(obj, path: str):
    """虚线路径取值（dict key / list 下标），miss 返回 _MISS。"""
    cur = obj
    for part in path.split('.'):
        if isinstance(cur, dict):
            cur = cur.get(part, _MISS)
        elif isinstance(cur, list) and part.isdigit():
            i = int(part)
            cur = cur[i] if i < len(cur) else _MISS
        else:
            return _MISS
        if cur is _MISS:
            return _MISS
    return cur


_MISS = object()


def _needle_hit(text: str, exp: dict) -> tuple:
    """needle|any_of|all_of 三形态统一判定。"""
    if 'any_of' in exp:
        needles = exp['any_of']
        hit = [n for n in needles if str(n) in text]
        return bool(hit), f'any_of {needles} → 命中 {hit}'
    if 'all_of' in exp:
        needles = exp['all_of']
        miss = [n for n in needles if str(n) not in text]
        return not miss, f'all_of {needles} → 缺 {miss}'
    n = exp.get('needle', '')
    return str(n) in text, f'needle {n!r} {"命中" if str(n) in text else "未命中"}'


def _field_cmp(val, exp: dict) -> tuple:
    if 'eq' in exp:
        return val == exp['eq'], f'{val!r} == {exp["eq"]!r}'
    if 'ne' in exp:
        return val != exp['ne'], f'{val!r} != {exp["ne"]!r}'
    if 'gt' in exp:
        try:
            return float(val) > float(exp['gt']), f'{val!r} > {exp["gt"]}'
        except (TypeError, ValueError):
            return False, f'{val!r} 不可比 gt {exp["gt"]}'
    if 'lt' in exp:
        try:
            return float(val) < float(exp['lt']), f'{val!r} < {exp["lt"]}'
        except (TypeError, ValueError):
            return False, f'{val!r} 不可比 lt {exp["lt"]}'
    if 'contains' in exp:
        return str(exp['contains']) in str(val), \
            f'{exp["contains"]!r} in {str(val)[:80]!r}'
    if exp.get('truthy'):
        ok = val not in (None, False, '', 0, _MISS)
        return ok, f'{val!r} truthy={ok}'
    return bool(val) and val is not _MISS, f'{val!r} 真值判定'


def eval_expect(exp: dict, records: list, session: str, cfg) -> tuple:
    """返回 (ok:bool, detail:str)。records=已执行步记录。"""
    kind = exp.get('kind')
    if kind == 'status_ok':
        bad = [r for r in records
               if r.get('status') != 'ok' and r.get('cmd') != 'nl']
        return (not bad), f'非 ok 步: {[r.get("cmd") for r in bad]}'

    if kind == 'json_field':
        idx = exp.get('step')
        src = None
        if idx is not None and 0 <= idx < len(records):
            src = records[idx].get('json')
        else:
            src = next((r.get('json') for r in reversed(records)
                        if r.get('json')), None)
        if src is None:
            return False, '无可判定 JSON 步输出'
        val = _dig(src, exp.get('path', ''))
        if val is _MISS:
            return False, f'path {exp.get("path")} miss'
        ok, det = _field_cmp(val, exp)
        return ok, f'path={exp.get("path")} {det}'

    if kind == 'eval_contains':
        expr = exp.get('expr') or 'document.body.innerText'
        r = run_dpx(['eval', expr], session, cfg.timeout)
        if r['status'] != 'ok':
            return False, f'eval 判定步失败: {r["code"]} {r["message"][:80]}'
        text = json.dumps(r.get('data'), ensure_ascii=False)
        ok, det = _needle_hit(text, exp)
        return ok, f'eval({expr[:40]}) {det}'

    if kind == 'url_contains':
        r = run_dpx(['eval', 'location.href'], session, cfg.timeout)
        if r['status'] != 'ok':
            return False, f'url 判定步失败: {r["code"]} {r["message"][:80]}'
        url = str((r.get('data') or {}).get('result') or '')
        ok, det = _needle_hit(url, exp)
        return ok, f'url={url[:90]} {det}'

    if kind == 'stdout_contains':
        idx = exp.get('step')
        src = records[idx].get('stdout', '') \
            if idx is not None and 0 <= idx < len(records) else \
            ''.join(r.get('stdout', '') for r in records[-2:])
        ok, det = _needle_hit(src, exp)
        return ok, det

    if kind == 'step_code':
        # 断言指定步归一化结果：status/code/message 合并文本做
        # needle 命中——用于"错误路径工作正常"类任务（如遮挡检测
        # 返回 CLICK_FAILED(hidden) 即算遮挡路径验证通过）。
        idx = exp.get('step')
        src = records[idx] if idx is not None and 0 <= idx < len(records) \
            else None
        if src is None:
            return False, f'step_code: 无 step {idx} 记录'
        blob = ' '.join(str(src.get(k) or '')
                        for k in ('status', 'code', 'message'))
        ok, det = _needle_hit(blob, exp)
        return ok, f'step[{idx}] {blob[:70]!r} {det}'

    return False, f'未知 expect.kind: {kind}'


# ── netlog ─────────────────────────────────────────────────────────────────

def netlog_lines(session: str) -> int:
    p = Path.home() / '.dp_cli' / f'netlog-{session}.jsonl'
    try:
        return sum(1 for _ in p.open('rb'))
    except OSError:
        return 0


# ── 任务执行 ──────────────────────────────────────────────────────────────

def extract_bugs(rec: dict) -> list:
    blob = ' '.join(str(rec.get(k) or '')
                    for k in ('code', 'message', 'detail', 'stdout'))
    return sorted(set(m.group(0).upper() if m.group(0).isascii()
                      else m.group(0) for m in BUG_RE.finditer(blob)))


def _net_down_check(session: str) -> str:
    """导航后探 chrome-error 落地页——网络层故障归因 NET_DOWN
    （区别于 WAIT_TIMEOUT 语义：chrome-error://chromewebdata 是
    浏览器已判导航失败，不该再等 90s 超时才认）。"""
    r = run_dpx(['eval', "location.protocol+'|'+location.href"],
                session, 15)
    if r['status'] != 'ok':
        return ''
    v = str((r.get('data') or {}).get('result') or '')
    return v if v.startswith('chrome-error:') else ''


def _risk_check(session: str) -> str:
    """探当前页是否风控/验证页——命中返回 'url | title' 串，未命中空串。"""
    r = run_dpx(['eval', "location.href+' | '+document.title"], session, 15)
    if r['status'] != 'ok' or not isinstance(r.get('data'), dict):
        return ''
    page = str(r['data'].get('result') or '')
    return page if RISK_RE.search(page) else ''


def run_task(task: dict, cfg) -> dict:
    tid = task.get('task_id', '?')
    steps = task.get('steps') or []
    t0 = time.monotonic()
    nlog0 = netlog_lines(cfg.session)
    records, asserts, decide_calls, bugs = [], [], [], []
    hard_fail = False
    risk_blocked = False

    for i, st in enumerate(steps):
        # 自然语言步——记档不执行
        if isinstance(st, str):
            st = {'nl': st}
        nl = st.get('nl') or st.get('step')
        if nl and 'cmd' not in st:
            records.append({'i': i, 'cmd': 'nl', 'status': 'skipped_nl',
                            'note': str(nl)[:120]})
            continue

        cmd = st.get('cmd')
        optional = bool(st.get('optional'))
        timeout = int(st.get('timeout') or cfg.timeout)

        if hard_fail or risk_blocked:
            records.append({'i': i, 'cmd': cmd, 'status': 'not_run'})
            continue

        if cmd == 'assert':
            exp = st.get('expect') or task.get('expect')
            if not exp:
                records.append({'i': i, 'cmd': 'assert', 'status': 'ok',
                                'assert': 'skip:no_expect'})
                continue
            ok, det = eval_expect(exp, records, cfg.session, cfg)
            asserts.append({'i': i, 'ok': ok, 'detail': det})
            records.append({'i': i, 'cmd': 'assert',
                            'status': 'ok' if ok else 'error',
                            'assert': det, 'optional': optional})
            if not ok and not optional:
                # assert 失败不中止——继续跑完记 partial
                pass
            continue

        # 多选择器 OR 回退：args[0] 为主选择器，locators 为候选追加
        # （DOM 漂移机制级防漂——站点改版后次候选自动顶上，记 locator_hit）
        alt_locs = [str(l) for l in (st.get('locators') or [])]
        base_args = [str(a) for a in (st.get('args') or [])]
        loc_cands = (base_args[:1] + alt_locs) if (alt_locs and base_args) \
            else [None]
        step_rec = {'i': i, 'cmd': cmd, 'calls': [], 'status': 'ok',
                    'optional': optional}
        used_loc = None
        for loc in loc_cands:
            st_eff = st if loc is None else \
                dict(st, args=[loc, *base_args[1:]])
            calls = step_calls(st_eff, cfg)
            worst = 'ok'
            healed = False
            for argv in calls:
                r = run_dpx(argv, cfg.session, timeout)
                if (r.get('code') == 'SESSION_DISCONNECTED'
                        and not healed):
                    # stale-attach 自愈：session 记的死 bridge/port 被
                    # 复用即判 DISCONNECTED（91 任务全灭归因）——停
                    # daemon 清绑定+重试一次，让 auto-connect 重建通道
                    healed = True
                    run_dpx(['daemon-stop'], cfg.session, 20)
                    try:
                        from dp_cli.session import delete_session
                        delete_session(cfg.session)
                    except Exception:
                        pass
                    cfg.attached = False
                    r = run_dpx(argv, cfg.session, timeout)
                call_rec = {'argv': argv, 'status': r['status'],
                            'code': r.get('code'),
                            'elapsed_ms': r['elapsed_ms'],
                            'stdout_tail': r.get('stdout', '')[-200:]}
                if r.get('message'):
                    call_rec['message'] = r['message'][:160]
                step_rec['calls'].append(call_rec)
                step_rec['elapsed_ms'] = \
                    step_rec.get('elapsed_ms', 0) + r['elapsed_ms']
                if r.get('data') is not None:
                    step_rec['json'] = {'status': 'ok', 'data': r['data']}
                    # 记录本批自建 tab id——跑批结束统一清场
                    # （只关 dpx 命令返回 id 的 tab，用户 tab 绝不动）
                    if cmd in ('open', 'tab-new') and r['status'] == 'ok':
                        new_tab_id = (r['data'] or {}).get('id')
                        if new_tab_id:
                            cfg.created_tabs.add(new_tab_id)
                    step_rec['data_summary'] = json.dumps(
                        r['data'], ensure_ascii=False)[:300]
                if cmd == 'decide' and r.get('data'):
                    d = r['data']
                    decide_calls.append({
                        'i': i, 'engine': d.get('engine'),
                        'conf': d.get('confidence'),
                        'tier': d.get('confidence_tier'),
                        'action': d.get('action'),
                        'escalate': d.get('should_escalate'),
                        'cached': d.get('cached'),
                        'elapsed_ms': r['elapsed_ms']})
                if r['status'] != 'ok':
                    worst = 'error'
                    step_rec['code'] = r.get('code')
                    if r.get('message'):
                        step_rec['message'] = r['message'][:160]
                    bugs += extract_bugs(r)
                    step_rec['bugs'] = sorted(set(
                        step_rec.get('bugs', []) + extract_bugs(r)))
                    break            # 子调用组内首败即停
            step_rec['status'] = worst
            used_loc = loc
            if worst == 'ok':
                break          # 首个可用选择器即停
        # chrome-error 归因：导航步后探落地页——chrome-error:// 是
        # 浏览器已判导航失败（网络层），归 NET_DOWN 非 WAIT_TIMEOUT
        if cmd in NAV_CMDS:
            nd = _net_down_check(cfg.session)
            if nd:
                step_rec['net_down'] = nd[:120]
                if step_rec.get('code') in (None, '', 'WAIT_TIMEOUT',
                                            'TIMEOUT'):
                    step_rec['code'] = 'NET_DOWN'
                if worst == 'ok':
                    worst = 'error'
                    step_rec['status'] = 'error'
                bugs.append('NET_DOWN')
                step_rec.setdefault('bugs', [])
                if 'NET_DOWN' not in step_rec['bugs']:
                    step_rec['bugs'].append('NET_DOWN')
        if len(loc_cands) > 1:
            step_rec['locator_tried'] = [l for l in loc_cands if l]
            if step_rec['status'] == 'ok' and used_loc != base_args[0]:
                step_rec['locator_hit'] = used_loc
        # 风控页探针：导航步后+任意步失败后各查一次——命中降级
        # RISK_CONTROL partial 而非产品 fail
        if not risk_blocked and (cmd in NAV_CMDS or worst != 'ok'):
            hit = _risk_check(cfg.session)
            if hit and RISK_RETRY_DELAY > 0:
                # 命中风控先 sleep 复探——滑块页常一次自过；仍中才降级
                time.sleep(RISK_RETRY_DELAY)
                retry_hit = _risk_check(cfg.session)
                if retry_hit:
                    hit = retry_hit
                else:
                    hit = ''
                    step_rec['risk_retried'] = 'cleared'
            if hit:
                risk_blocked = True
                _quar_mark(task.get('site'))
                step_rec['quarantined'] = task.get('site')
                step_rec['risk_page'] = hit[:160]
                bugs.append('RISK_CONTROL')
                step_rec.setdefault('bugs', [])
                if 'RISK_CONTROL' not in step_rec['bugs']:
                    step_rec['bugs'].append('RISK_CONTROL')
        if worst != 'ok' and not optional:
            hard_fail = True
            step_rec['fatal'] = True
        records.append(step_rec)

    # 任务级 expect：无 assert 步消费时在末尾统一判定
    expects = []
    if not any(r.get('cmd') == 'assert' for r in records) and task.get('expect'):
        ok, det = eval_expect(task['expect'], records, cfg.session, cfg)
        expects.append({'kind': task['expect'].get('kind'), 'ok': ok,
                        'detail': det})
    for a in asserts:
        expects.append({'kind': 'assert', 'ok': a['ok'],
                        'detail': a['detail']})

    n_ok = sum(1 for r in records if r.get('status') == 'ok')
    n_run = sum(1 for r in records
                if r.get('status') not in ('skipped_nl', 'not_run'))
    if risk_blocked:
        verdict = 'partial'
    elif hard_fail:
        verdict = 'fail'
    elif expects and all(e['ok'] for e in expects):
        verdict = 'pass'
    elif expects:
        verdict = 'partial'
    else:
        verdict = 'pass' if n_ok == n_run else 'partial'

    return {'task_id': tid, 'site': task.get('site'),
            'goal': task.get('goal'), 'verdict': verdict,
            'total_ms': int((time.monotonic() - t0) * 1000),
            'steps_n': n_run, 'steps_ok': n_ok,
            'expects': expects, 'decide_calls': decide_calls,
            'bugs': sorted(set(bugs)),
            'netlog_delta': netlog_lines(cfg.session) - nlog0,
            'steps': records}


# ── 汇总 ─────────────────────────────────────────────────────────────────

def summarize(results: list) -> dict:
    n = len(results)
    v = {'pass': 0, 'partial': 0, 'fail': 0}
    for r in results:
        v[r['verdict']] = v.get(r['verdict'], 0) + 1

    # 命令耗时分布
    cmd_ms = {}
    for r in results:
        for s in r['steps']:
            if s.get('elapsed_ms') is not None:
                cmd_ms.setdefault(s['cmd'], []).append(s['elapsed_ms'])
    timing = {c: {'n': len(v_), 'mean_ms': int(statistics.mean(v_)),
                  'p50_ms': int(statistics.median(v_)),
                  'max_ms': max(v_)}
              for c, v_ in sorted(cmd_ms.items(),
                                  key=lambda kv: -statistics.mean(kv[1]))}

    # decide / reranker 表现
    calls = [c for r in results for c in r['decide_calls']]
    eng_stat = {}
    for c in calls:
        e = eng_stat.setdefault(c.get('engine') or 'unknown',
                                {'n': 0, 'confs': [], 'tiers': {},
                                 'escalate': 0, 'cached': 0})
        e['n'] += 1
        if isinstance(c.get('conf'), (int, float)):
            e['confs'].append(c['conf'])
        t = c.get('tier') or 'n/a'
        e['tiers'][t] = e['tiers'].get(t, 0) + 1
        e['escalate'] += 1 if c.get('escalate') else 0
        e['cached'] += 1 if c.get('cached') else 0
    for e in eng_stat.values():
        e['avg_conf'] = round(statistics.mean(e['confs']), 3) \
            if e['confs'] else None
        e.pop('confs')

    bug_counts = {}
    for r in results:
        for b in r['bugs']:
            bug_counts[b] = bug_counts.get(b, 0) + 1

    return {'tasks_n': n, 'verdicts': v,
            'pass_rate': round(v['pass'] / n, 3) if n else 0,
            'cmd_timing': timing, 'decide_engines': eng_stat,
            'decide_calls_n': len(calls),
            'bug_counts': dict(sorted(bug_counts.items(),
                                      key=lambda kv: -kv[1])),
            'netlog_delta_total': sum(r['netlog_delta'] for r in results)}


def print_table(results: list, summary: dict, report: Path):
    w = max((len(r['task_id']) for r in results), default=8)
    print('\n' + '=' * 78)
    print(f'{"task_id":<{w}}  verdict  total_ms  steps  netlog+  bugs')
    print('-' * 78)
    for r in results:
        print(f'{r["task_id"]:<{w}}  {r["verdict"]:<8}  '
              f'{r["total_ms"]:>7}  {r["steps_ok"]}/{r["steps_n"]:<4} '
              f'{r["netlog_delta"]:>6}  {",".join(r["bugs"]) or "-"}')
    print('-' * 78)
    v = summary['verdicts']
    print(f'verdict: pass {v.get("pass", 0)} / partial '
          f'{v.get("partial", 0)} / fail {v.get("fail", 0)}   '
          f'pass_rate={summary["pass_rate"] * 100:.0f}%')
    if summary['cmd_timing']:
        top = ', '.join(f'{c} avg={t["mean_ms"]}ms(n={t["n"]})'
                        for c, t in list(summary['cmd_timing'].items())[:6])
        print(f'耗时分布: {top}')
    if summary['decide_engines']:
        eng = '; '.join(
            f'{e}: n={s["n"]} avg_conf={s["avg_conf"]} '
            f'tiers={s["tiers"]} escalate={s["escalate"]}'
            for e, s in summary['decide_engines'].items())
        print(f'decide 引擎: {eng}')
    if summary['bug_counts']:
        print('bug 分类: ' + ', '.join(f'{k}×{v_}' for k, v_ in
                                       summary['bug_counts'].items()))
    print(f'netlog 增量: +{summary["netlog_delta_total"]} 行')
    print(f'report → {report}')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tasks', default=str(DEFAULT_TASKS),
                    help='任务 jsonl 路径')
    ap.add_argument('--session', '-S', default='taskpool',
                    help='共用 session 池名（默认 taskpool）')
    ap.add_argument('--only', default=None,
                    help='只跑逗号分隔的 task_id 子集')
    ap.add_argument('--browser', default='/usr/bin/google-chrome',
                    help='--auto-connect 注入的浏览器路径')
    ap.add_argument('--auto-connect', dest='auto_connect',
                    action='store_true', default=True)
    ap.add_argument('--no-auto-connect', dest='auto_connect',
                    action='store_false')
    ap.add_argument('--timeout', type=int, default=120,
                    help='单步超时秒数（默认 120）')
    ap.add_argument('--daemon', dest='daemon', action='store_true',
                    default=True,
                    help='预启动 daemon（netlog arm+命令免启动税，默认开）')
    ap.add_argument('--no-daemon', dest='daemon', action='store_false')
    cfg = ap.parse_args()
    cfg.attached = False      # auto-connect attach 状态（跨任务共享 session）
    cfg.pinned = False        # active_tab 绑定状态

    tasks = []
    for line in Path(cfg.tasks).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line:
            tasks.append(json.loads(line))
    if cfg.only:
        wanted = set(cfg.only.split(','))
        tasks = [t for t in tasks if t.get('task_id') in wanted]
    if not tasks:
        print('无匹配任务', file=sys.stderr)
        return 2

    print(f'任务池: {len(tasks)} 条  session={cfg.session}  '
          f'auto_connect={cfg.auto_connect}  daemon={cfg.daemon}')

    # preflight：先 attach（首个任务的 open 步也会做，这里只是让
    # daemon-start 有 session 可依）；daemon 常驻 → netlog arm +
    # decide 模型免冷启。失败不阻断——后续步如实记 fail。
    preflight = []
    if cfg.auto_connect and not cfg.attached:
        r = run_dpx(['open', '--auto-connect', '--browser', cfg.browser],
                    cfg.session, cfg.timeout)
        cfg.attached = r['status'] == 'ok'
        preflight.append({'argv': ['open', '--auto-connect'],
                          'status': r['status'], 'code': r.get('code'),
                          'elapsed_ms': r['elapsed_ms']})
        print(f'  preflight attach: {r["status"]} '
              f'{r.get("code") or ""}'.rstrip(), flush=True)
    if cfg.daemon and cfg.attached:
        r = run_dpx(['daemon-start'], cfg.session, 30)
        preflight.append({'argv': ['daemon-start'], 'status': r['status'],
                          'code': r.get('code'),
                          'elapsed_ms': r['elapsed_ms']})
        print(f'  preflight daemon-start: {r["status"]} '
              f'{r.get("code") or ""}'.rstrip(), flush=True)

    cfg.created_tabs = set()
    results = []
    breaker_codes = {'WAIT_TIMEOUT', 'NET_DOWN', 'TIMEOUT'}
    consec, last_code, aborted = 0, None, False
    for t in tasks:
        site = t.get('site')
        quar = _quar_load()
        if site and site in quar:
            left = int(QUAR_TTL - (time.time() - quar[site]))
            print(f'  ▶ {t.get("task_id")} ... quarantined({site} '
                  f'剩余 {left}s)', flush=True)
            results.append({
                'task_id': t.get('task_id'), 'site': site,
                'goal': t.get('goal'), 'verdict': 'partial',
                'total_ms': 0, 'steps_n': 0, 'steps_ok': 0,
                'expects': [], 'decide_calls': [],
                'bugs': ['RISK_CONTROL'], 'netlog_delta': 0,
                'quarantined': True, 'steps': []})
            continue
        print(f'  ▶ {t.get("task_id")} ...', flush=True)
        r = run_task(t, cfg)
        print(f'    {r["verdict"]}  {r["total_ms"]}ms  '
              f'{r["steps_ok"]}/{r["steps_n"]} 步  '
              f'netlog+{r["netlog_delta"]}'
              + (f'  bugs={r["bugs"]}' if r['bugs'] else ''), flush=True)
        results.append(r)
        # 全局熔断：连续 3 任务同码超时类失败（网络 blip 时不再
        # 90s×N 串行硬等烧满窗口）——提前收尾照样写 partial 报告
        tcode = next((s.get('code') for s in r.get('steps', [])
                      if s.get('status') == 'error' and s.get('code')),
                     None)
        if r.get('quarantined'):
            pass
        elif tcode in breaker_codes and tcode == last_code:
            consec += 1
        elif tcode in breaker_codes:
            consec, last_code = 1, tcode
        else:
            consec, last_code = 0, None
        if consec >= 3:
            aborted = True
            print(f'  ⛔ 熔断：连续 {consec} 任务同码 {tcode}——'
                  f'疑似环境级故障，批跑提前收尾', flush=True)
            break

    # 清场：关本批自建 tab（只关 dpx 命令返回 id 的——用户 tab
    # 绝不动；tab-close 走绑定会话，失败如实忽略）
    if cfg.created_tabs:
        print(f'  清场: 关闭 {len(cfg.created_tabs)} 个本批 tab',
              flush=True)
        for tid in sorted(cfg.created_tabs):
            run_dpx(['tab-close', tid], cfg.session, 15)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    report = REPORT_DIR / f'taskpool_{ts}.json'
    summary = summarize(results)
    if aborted:
        summary['aborted'] = True
        summary['abort_reason'] = \
            f'consecutive {consec} tasks code={last_code}'
    report.write_text(json.dumps(
        {'generated_at': datetime.now().isoformat(timespec='seconds'),
         'session': cfg.session, 'tasks_file': cfg.tasks,
         'preflight': preflight,
         'summary': summary, 'tasks': results},
        ensure_ascii=False, indent=2), encoding='utf-8')
    print_table(results, summary, report)
    return 0 if summary['verdicts'].get('fail', 0) == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
