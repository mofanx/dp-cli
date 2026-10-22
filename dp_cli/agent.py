# -*- coding:utf-8 -*-
"""agent 决策原语：agent-state / decide / agent-setup。

Jev 范式落地——决策模型只从**给定候选**里选，绝不生成 selector/JS：
  snapshot 控件表 → (engine) → {action, ref, probs, done, stuck}

引擎：
  external  默认——吐 JSON 请求，从 --answer/--answer-file/stdin 收答复，
            零依赖；与 julia1 predict / Jev API / LLM 同构协议
  julia     本地 Julia-1——julia1 predict 子进程调用（agent-setup 注册）
  jev       TypeSafe API——TYPESAFE_API_KEY

done：模型 done OR + 状态哈希变化检测（refs 版本戳）。
stuck：snapshot diff + churn 去噪 + N 步连续门槛——确定性信号，
        不外包给概率模型。
TYPE 动作只回 ref——value 由上层补，decide 不猜。
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import click

from dp_cli.output import ok, error, debug, _dump, _write_err
from dp_cli.session import load_session, save_session, save_refs, load_refs
from dp_cli.commands._utils import session_option, _get_page

_MODELS_DIR = Path.home() / '.dp_cli' / 'models'
_TOP_K = 20            # Julia-1 选项数上限（2-20）
_ACTIONS = ['click', 'type', 'select', 'scroll_up', 'scroll_down',
            'back', 'wait', 'done', 'blocked']
_CONF_FLOOR = 0.35     # 低于此置信 → AGENT_UNSURE
_STUCK_WINDOW = 3      # 连续 N 步哈希不变 → stuck
_INTERACTIVE_ROLES = {
    'link', 'button', 'textbox', 'searchbox', 'combobox', 'listbox',
    'checkbox', 'radio', 'switch', 'tab', 'menuitem', 'option',
    'spinbutton', 'slider', 'treeitem',
}


# ── 控件表（Jev 格式）───────────────────────────────────────

def _page_state(page, session, max_opts=_TOP_K):
    """取 a11y 快照 → Jev 式 {state, options[{ref,role,name,current}]}。"""
    from dp_cli.snapshot.a11y import take_a11y_snapshot
    data = take_a11y_snapshot(page, max_depth=12)
    nodes = data.get('tree') or data.get('elements') or []
    if isinstance(nodes, dict):
        nodes = nodes.get('children', [])
    opts, refs = [], {}
    i = 0
    for n in _walk(nodes):
        role = (n.get('role') or '').lower()
        if role not in _INTERACTIVE_ROLES:
            continue
        i += 1
        if i > max_opts:
            break
        name = (n.get('name') or '').strip()[:120]
        cur = n.get('value')
        entry = {'ref': i, 'role': role, 'name': name}
        if cur not in (None, ''):
            entry['current'] = str(cur)[:80]
        opts.append(entry)
        ref_entry = {'locator': n.get('locator') or f'css:[data-dpx-ref="{i}"]',
                     'role': role, 'name': name,
                     'backendNodeId': n.get('backendNodeId')}
        if n.get('locator'):
            ref_entry['locator'] = n['locator']
        refs[str(i)] = ref_entry
    url = page.url or ''
    title = ''
    try:
        title = page.title or ''
    except Exception:
        pass
    # refs 版本戳：URL+控件签名——decide/后续动作据此检测过期
    sig = hashlib.sha1(
        f"{url}|{len(opts)}|{','.join(o['name'] for o in opts[:10])}"
        .encode()).hexdigest()[:12]
    if refs:
        save_refs(session, url, refs)
        sess = load_session(session) or {}
        sess['refs_epoch'] = {'sig': sig, 'url': url, 'at': time.time()}
        save_session(session, sess)
    return {'state': {'url': url, 'title': title, 'sig': sig},
            'options': opts, 'epoch': sig,
            'truncated': len([n for n in _walk(nodes)
                              if (n.get('role') or '').lower()
                              in _INTERACTIVE_ROLES]) > max_opts}


def _walk(node):
    if isinstance(node, list):
        for x in node:
            yield from _walk(x)
        return
    if not isinstance(node, dict):
        return
    yield node
    for ch in node.get('children') or []:
        yield from _walk(ch)


# ── 引擎 ────────────────────────────────────────────────────

def _engine_external(request, answer, answer_file):
    """零依赖协议：输出 request JSON，收答复（--answer/文件/stdin）。"""
    if answer:
        raw = answer
    elif answer_file:
        raw = Path(answer_file).read_text(encoding='utf-8')
    else:
        # 交互态：请求写 stderr，答复从 stdin 读一行
        _write_err(json.dumps(
            {'_request': 'decide-answer',
             'hint': '请给一行 JSON，如 {"action":"click","target":3,'
                     '"done":false,"stuck":false,"probs":{}}\n'},
            ensure_ascii=False) + '\n')
        raw = sys.stdin.readline().strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        return {'_error': f'答复 JSON 解析失败: {e}'}


def _engine_julia(request, session):
    """Julia-1——julia1 predict 子进程（sess['agent_julia_cmd']）。"""
    sess = load_session(session) or {}
    cmd = sess.get('agent_julia_cmd')
    if not cmd:
        return {'_error': 'Julia 引擎未配置——先运行 '
                '`dpx agent-setup --engine julia --cmd <julia1路径>`'}
    try:
        p = subprocess.run(
            [cmd, 'predict'], input=json.dumps(request) + '\n',
            capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            return {'_error': f'julia1 退出 {p.returncode}: '
                    f'{p.stderr[:200]}'}
        return json.loads(p.stdout.strip().split('\n')[-1])
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return {'_error': f'julia1 调用失败: {e}'}


def _engine_jev(request):
    """TypeSafe Jev API——TYPESAFE_API_KEY。"""
    key = os.environ.get('TYPESAFE_API_KEY')
    if not key:
        return {'_error': '缺 TYPESAFE_API_KEY 环境变量'}
    import urllib.request
    try:
        req = urllib.request.Request(
            'https://api.typesafe.ai/v1/decide',
            data=json.dumps(request).encode(),
            headers={'Content-Type': 'application/json',
                     'Authorization': f'Bearer {key}'},
            method='POST')
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {'_error': f'Jev API 失败: {e}'}


_ROLE_ACTION = {'textbox': 'type', 'combobox': 'type', 'searchbox': 'type',
                'spinbutton': 'type', 'listbox': 'select'}


def _ask_questions(state, goal, engine, session, answer, answer_file):
    """扇出三问（action/goal done/stuck）→ 规范答复。

    julia 引擎走归因修复形态：只问 target+done/stuck（实测 action 问
    准确率不可修，26.7%→60%），action 由选中元素的 role 代码映射；
    target criteria 用裸元素名（role 前缀实测 -27pp）。
    """
    if engine == 'julia':
        request = {
            'state': f"Page: {state['state']['title']} "
                     f"({state['state']['url']})\nGoal: {goal}",
            'questions': {
                'done': {'type': 'noul',
                         'instructions': '目标已完成？'},
                'stuck': {'type': 'noul',
                          'instructions': '是否卡住/循环？'},
                'target': {'type': 'choice',
                           'instructions': f'目标: {goal}。'
                                           '哪个元素推进目标？选编号',
                           'criteria': {str(o['ref']): o['name']
                                        for o in state['options']}},
            },
        }
        resp = _engine_julia(request, session)
        if 'action' not in resp and 'target' in resp:
            if str(resp.get('done')) in ('true', 'True') or \
                    resp.get('done') is True:
                resp['action'] = 'done'
            else:
                try:
                    t = int(resp['target'])
                except (TypeError, ValueError):
                    t = -1
                role = next((o['role'] for o in state['options']
                             if o['ref'] == t), '')
                resp['action'] = _ROLE_ACTION.get(role, 'click')
        return resp, request
    opts = [f"{o['ref']}. {o['role']}: {o['name']}"
            + (f" [={o['current']}]" if o.get('current') else '')
            for o in state['options']]
    request = {
        'state': f"URL: {state['state']['url']}\n"
                 f"Title: {state['state']['title']}",
        'questions': {
            'action': {'type': 'choice',
                       'instructions': f'目标: {goal}。下一步做哪个动作？',
                       'criteria': {a: a for a in _ACTIONS}},
            'target': {'type': 'choice',
                       'instructions': f'目标: {goal}。'
                                       '在哪些元素上执行？选编号',
                       'criteria': {str(o['ref']): s
                                    for o, s in
                                    zip(state['options'], opts)}},
            'done': {'type': 'noul',
                     'instructions': '目标已完成？'},
            'stuck': {'type': 'noul',
                      'instructions': '是否卡住/循环？'},
        },
    }
    if engine == 'external':
        return _engine_external(request, answer, answer_file), request
    if engine == 'jev':
        return _engine_jev(request), request
    return {'_error': f'未知引擎: {engine}'}, request


def _state_sig(sess_name):
    """refs epoch 签名——done/stuck 的确定性信号。"""
    sess = load_session(sess_name) or {}
    ep = sess.get('refs_epoch') or {}
    return ep.get('sig')


def _record_hash(sess_name, sig):
    """滚动哈希窗口——stuck 检测。"""
    sess = load_session(sess_name) or {}
    hist = sess.get('agent_hash_hist', [])
    hist.append(sig)
    sess['agent_hash_hist'] = hist[-_STUCK_WINDOW:]
    save_session(sess_name, sess)
    return hist


def register(cli):
    @cli.command('agent-state')
    @session_option
    @click.option('--top-k', default=_TOP_K, show_default=True,
                  help='控件表最大条目（Julia-1 上限 20）')
    def agent_state(session, top_k):
        """输出 Jev 格式控件表（state+options[]+epoch 版本戳）。

        决策模型统一输入——Julia-1/Jev API/LLM 都能消费；
        ref:N 与现有 snapshot ref 同一编号空间。
        """
        page = _get_page(session)
        st = _page_state(page, session, max_opts=top_k)
        ok(st)

    @cli.command('decide')
    @click.argument('goal')
    @session_option
    @click.option('--engine',
                  type=click.Choice(['external', 'julia', 'jev']),
                  default=None, help='决策引擎（默认=已 setup 的）')
    @click.option('--answer', default=None,
                  help='external 引擎内联答复 JSON')
    @click.option('--answer-file', default=None,
                  help='external 引擎答复文件路径')
    @click.option('--min-conf', default=_CONF_FLOOR, show_default=True,
                  help='最低置信度，低于此 AGENT_UNSURE')
    @click.option('--min-margin', default=0.15, show_default=True,
                  help='top1-top2 margin 阈值（歧义判定，比绝对置信可靠）')
    @click.option('--cascade', is_flag=True,
                  help='级联模式：Julia 粗筛 top-3 候选集给上层终裁')
    @click.option('--top-k', default=_TOP_K, show_default=True)
    def decide(goal, session, engine, answer, answer_file,
               min_conf, min_margin, cascade, top_k):
        """【experimental 未验证准确率】单步决策：扇出三问 → {op,ref,probs}。

        ⚠ 波次 0 实测：Julia-1 对浏览器控件任务准确率 ~40%，未达立项
        阈值（≥85% LLM 基线）——本命令保留用于外部引擎/benchmark，
        不建议生产使用。

        \b
        引擎:
          external  吐请求收答复（--answer/--answer-file/stdin），零依赖
          julia     本地 Julia-1（先 dpx agent-setup --engine julia）
          jev       TypeSafe API（TYPESAFE_API_KEY）

        \b
        示例:
          dpx decide "搜索 Python" --engine external \\
              --answer '{"action":"type","target":4,"done":false}'
          dpx decide "点登录" --engine julia
        """
        page = _get_page(session)
        sess = load_session(session) or {}
        eng = engine or sess.get('agent_engine') or 'external'
        state = _page_state(page, session, max_opts=top_k)
        prev_sig = _state_sig(session)
        # --cascade：Julia 粗筛 top-3 候选集回传上层终裁，
        # 不自动执行（top-3 召回实测 100%，top-1 仅 60%）
        if cascade:
            resp, request = _ask_questions(
                state, goal, eng, session, answer, answer_file)
            if '_error' in resp:
                error('决策引擎失败', code='DECIDE_FAILED',
                      detail=resp['_error'])
                return
            probs_t = (resp.get('probs') or {}).get('target') or {}
            ranked = sorted(probs_t.items(), key=lambda x: -x[1])[:3]
            if not ranked and resp.get('target'):
                ranked = [(str(resp['target']),
                           resp.get('confidence') or 0)]
            ok({'candidates': [
                {'ref': int(k) if str(k).isdigit() else k,
                 'prob': round(float(v), 4),
                 **next((o for o in state['options']
                         if str(o['ref']) == str(k)), {})}
                for k, v in ranked],
                'epoch': state['epoch'],
                'engine': eng, 'goal': goal,
                'note': '级联模式——请上层引擎对候选集终裁后执行'})
            return
        resp, request = _ask_questions(state, goal, eng, session,
                                       answer, answer_file)
        if '_error' in resp:
            error('决策引擎失败', code='DECIDE_FAILED',
                  detail=resp['_error'])
            return
        action = str(resp.get('action') or resp.get('op') or '').lower()
        target = resp.get('target')
        done = bool(resp.get('done'))
        stuck_m = bool(resp.get('stuck'))
        probs = resp.get('probs') or {}
        conf = float(resp.get('confidence')
                     or probs.get(action, 0.0) or 0.0)
        # done = 模型 done OR + 状态哈希变化（refs epoch 变了就是真动了）
        cur_sig = state['epoch']
        hist = _record_hash(session, cur_sig)
        hash_changed = cur_sig != prev_sig
        # stuck = 连续 N 步哈希不变（确定性信号，不外包给模型）
        stuck_h = len(hist) >= _STUCK_WINDOW and len(set(hist)) == 1
        result = {
            'action': action, 'ref': target, 'confidence': conf,
            'probs': probs, 'done': done or (not hash_changed and not action),
            'done_hash_changed': hash_changed,
            'stuck': stuck_m or stuck_h, 'epoch': cur_sig,
            'options_count': len(state['options']),
            'truncated': state['truncated'], 'engine': eng,
        }
        # TYPE 动作只回 ref——value 由上层补
        if action in ('type', 'type_text', 'fill'):
            result['needs_value'] = True
        # margin 门：top1-top2 概率差——绝对置信不可信
        # （实测 conf=1.00 也会错，margin 能拦 3/4 miss）
        _pa = sorted((probs.get('action') or {}).values(), reverse=True)
        _pt = sorted((probs.get('target') or {}).values(), reverse=True)
        if _pa and _pt:
            margin = min(_pa[0] - (_pa[1] if len(_pa) > 1 else 0),
                         _pt[0] - (_pt[1] if len(_pt) > 1 else 0))
            msrc = 'probs'
        elif _pa or _pt:
            one = _pa or _pt
            margin = one[0] - (one[1] if len(one) > 1 else 0)
            msrc = 'single_side'   # 单边 probs——非真 margin
        else:
            margin = conf
            msrc = 'conf_fallback'  # 无 probs——静默降级为置信度
        result['margin'] = round(margin, 4)
        result['margin_source'] = msrc
        if not done and (conf and conf < min_conf
                         or margin < min_margin):
            error(f'置信度 {conf:.2f} / margin {margin:.2f} 低于阈值——'
                  '候选不足或页面不适合当前决策模型',
                  code='AGENT_UNSURE',
                  detail=json.dumps(result, ensure_ascii=False))
            return
        ok(result)

    @cli.command('agent-setup')
    @session_option
    @click.option('--engine', 'eng',
                  type=click.Choice(['julia', 'jev', 'external']),
                  required=True, help='注册的默认引擎')
    @click.option('--cmd', default=None,
                  help='julia1 二进制路径（engine=julia 时必填）')
    def agent_setup(session, eng, cmd):
        """注册决策引擎到 session（模型/路径按需下载显式做）。

        \b
        示例:
          dpx agent-setup --engine julia --cmd ~/bin/julia1

        \b
        Julia-1 运行时依赖（必须 pin，否则 encoder 崩溃）:
          pip install "transformers==5.0.0" torch safetensors
          # 权重: huggingface-cli download SupersonicLabs/Julia-1
          dpx agent-setup --engine jev        # 用 TYPESAFE_API_KEY
          dpx agent-setup --engine external   # 外部 loop 驱动
        """
        if eng == 'julia':
            if not cmd:
                error('--engine julia 需 --cmd 指定 julia1 路径',
                      code='INVALID_ARGS')
                return
            if not Path(cmd).exists():
                error(f'julia1 不存在: {cmd}——先下载运行时',
                      code='SETUP_FAILED')
                return
        sess = load_session(session) or {}
        sess['agent_engine'] = eng
        if cmd:
            sess['agent_julia_cmd'] = cmd
        save_session(session, sess)
        ok({'engine': eng, 'cmd': cmd}, msg='引擎已注册')
