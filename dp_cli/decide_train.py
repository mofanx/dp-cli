# -*- coding: utf-8 -*-
"""decide 训练数据采集——交互命令即标注源（opt-in）。

开启：DPX_RECORD_DECIDE=1
落盘：~/.dp_cli/decide_train.jsonl
  {ts, session, state:{url, refs:[{ref,role,name}…]}, chosen:{cmd, argv}}
导出（decide-train-export）：reranker 训练格式
  {query, positive, negatives[]}——query=state 描述拼接，
  positive=chosen 命令文本，negatives=同 state 其他候选文本。

hard-case 被动落盘（默认开，DPX_HARD_CASES=0 关闭）：
decide 低置信候选（conf<0.5 或 top2 margin<0.1）写
~/.dp_cli/hard_cases.jsonl——调试/标注原料非训练承诺，
与 decide_train.jsonl 并行两份职责，bench/review_hard_cases.py
定期汇总输出 LLM 标注清单（--emit-teacher 出待标注模板）。
另有高置信盲区反查通道：result 带 post_delta=[]（录制形态
_record_decide_row 双快照齐才挂——执行后页面零变化）且
conf≥0.8 → triggers 'high_conf_no_effect'，补 conf=1.0
自信错选采集盲区；post_delta 字段缺失不触发防误伤。
escalate 反哺通道：decide 升级（should_escalate）后上层自答
成功 → decide-feedback 命令/log_escalate_feedback 回写
prov='escalate_feedback'+feedback='escalated' 行（fail-closed
须 success 证据旗，见 bench/DATA_SCHEMA.md）。
schema_v=2 起 candidates 录全池（state['options'] 完整集，
计分集带 score、未计分 score 缺省）；旧行无 schema_v 字段
=v1 probs top3 截断版，下游解析按 v 分流。

pre_state/post_state 双快照齐时行内附 post_delta 字段（
state_delta() 判据：url/hash/refs_epoch/DOM sig 任一变化=有
执行后证据，[]=空快照不冒充 verified）；三形态数据契约见
bench/DATA_SCHEMA.md。
"""
import json
import os
import threading
import time
from pathlib import Path

from dp_cli.session import get_session_dir

_FLAG = 'DPX_RECORD_DECIDE'
_SKIP = {'ping', 'help', 'exit', 'quit', 'snapshot', 'agent-state',
         'decide-train-export'}
_MAX_LINES = 4000       # ring 上限
_TRIM_HIGH = 5000       # 超过即截到 _MAX_LINES（复用 netlog 滚动模式）


def _maybe_trim(f: Path) -> None:
    """decide_train.jsonl 无限增长防护——超水位截留尾部近似 ring。

    tmp+rename 原子重写：并发读（export_pairs）永远见完整文件。
    录制是旁路增强，trim 失败静默不阻塞主链路。
    """
    try:
        lines = f.read_text(encoding='utf-8').splitlines()
    except OSError:
        return
    if len(lines) <= _TRIM_HIGH:
        return
    try:
        tmp = f.with_suffix(f.suffix + '.tmp')
        tmp.write_text('\n'.join(lines[-_MAX_LINES:]) + '\n',
                       encoding='utf-8')
        tmp.replace(f)
    except OSError:
        pass


def enabled() -> bool:
    return os.environ.get(_FLAG) == '1'


def _file() -> Path:
    return get_session_dir() / 'decide_train.jsonl'


# ── 快照 diff 证据判定（断言可执行性）─────────────────────────────
# URL/hash/refs_epoch/DOM sig 任一变化=有执行后状态证据；全同=
# 动作疑似未生效的空快照（不冒充 verified）。bench/verified_data.
# _state_delta 与本函数同判据（bench 侧自包含副本，勿分叉）。
_STATE_KEYS = ('url', 'hash', 'refs_epoch', 'sig', 'dom_sig')


def state_delta(pre: dict, post: dict) -> list:
    """pre/post 快照 diff 字段清单——断言可执行性判定。

    返回 _STATE_KEYS 序的变化字段名；任一侧非 dict → []。
    post 侧字段缺失不计变化（快照残缺≠状态变）；pre 缺字段而
    post 有值算变化（无旧值可比按新状态计）。
    """
    if not isinstance(pre, dict) or not isinstance(post, dict):
        return []
    return [k for k in _STATE_KEYS
            if post.get(k) is not None and post.get(k) != pre.get(k)]


# ── hard-case 被动落盘（默认开）──────────────────────────────────
# decide 低置信候选自动攒盘：conf<0.5 或 top2 margin<0.1 →
# ~/.dp_cli/hard_cases.jsonl。调试数据非训练承诺——被动攒默认开，
# DPX_HARD_CASES=0 显式关闭；与 decide_train.jsonl（opt-in 训练
# 录制）并行，两个文件两份职责。
_HARD_FLAG = 'DPX_HARD_CASES'
_HARD_CONF = 0.5         # conf 低于此值 → hard case
_HARD_MARGIN = 0.1       # top2 分差低于此值 → hard case（歧义）
_HARD_NO_EFFECT = 0.8    # conf≥此值且 post_delta=[] → 高置信
                         # 零效果反查（自信错选疑似通道）
_HARD_POOL_CAP = 300     # candidates 全池落盘上限——对齐
                         # agent._EMBED_POOL_MAX 防病理页单行爆炸
_HARD_SCHEMA_V = 2       # candidates 契约版本：无此字段的旧行=v1
                         # （probs top3 截断）；v2=全池候选+计分标记
_hard_lock = threading.Lock()


def hard_cases_enabled() -> bool:
    """hard-case 落盘总闸——默认开（被动攒调试数据），'0' 显式关。"""
    return os.environ.get(_HARD_FLAG, '1') != '0'


def _hard_file() -> Path:
    return Path.home() / '.dp_cli' / 'hard_cases.jsonl'


def _hard_lock_path() -> Path:
    return Path.home() / '.dp_cli' / 'hard_cases.lock'


def _hard_append(row: dict) -> None:
    """hard_cases.jsonl 追加一行——进程内线程锁 + 跨进程
    spawn-lock 双保险（netlog 同款），serve/daemon/CLI 并存写
    不撕裂；OSError/锁超时静默不阻塞调用方。"""
    f = _hard_file()
    with _hard_lock:
        try:
            from dp_cli.daemon import _acquire_spawn_lock
            f.parent.mkdir(parents=True, exist_ok=True)
            with _acquire_spawn_lock(_hard_lock_path(), timeout=5):
                with f.open('a', encoding='utf-8') as fp:
                    fp.write(json.dumps(row, ensure_ascii=False) + '\n')
                _maybe_trim(f)
        except (OSError, TimeoutError):
            pass


def _ref_meta(session: str, state: dict = None) -> dict:
    """ref→{role,name} 查名表：decide 内嵌 state.options 优先；
    无 state（录制路径）退 load_refs——与录制同源数据。"""
    if state:
        opts = state.get('options') or []
        if opts:
            return {str(o.get('ref')): o for o in opts}
    try:
        from dp_cli.session import load_refs
        return {str(k): v for k, v in (load_refs(session) or {}).items()}
    except Exception:
        return {}


def _hard_url(session: str, state: dict = None):
    """页面 url：state 内嵌优先，退 session refs_epoch（_page_state
    在 decide 时已写入——同一次快照的 url，零额外 IO 成本）。"""
    url = ((state or {}).get('state') or {}).get('url')
    if url:
        return url
    try:
        from dp_cli.session import load_session
        return ((load_session(session) or {}).get('refs_epoch')
                or {}).get('url')
    except Exception:
        return None


def _maybe_log_hard_case(result: dict, session: str, goal: str = None,
                         state: dict = None, unsure: bool = False) -> None:
    """decide 低置信候选被动落盘——主动学习 hard-case 原料。

    触发：conf<0.5 或 top2 margin<0.1（result['margin'] 已是
    action/target 双侧 min，conf_fallback 降级语义由
    margin_source 字段透传给 review 侧判读，此处不重复分流），
    或 conf≥0.8 且 result 带 post_delta=[]（执行后页面零变化=
    动作未生效疑似自信错选——补 conf=1.0 盲区；post_delta 只在
    录制双快照形态由 _record_decide_row 挂载，字段缺失不触发）。
    一切异常自吞——旁路调试采集绝不炸 decide 主链路；进程内
    线程锁 + 跨进程 spawn-lock（netlog 同款双保险）防 serve/
    daemon/CLI 并存写撕裂。
    """
    try:
        if not hard_cases_enabled() or not isinstance(result, dict):
            return
        conf = result.get('confidence')
        margin = result.get('margin')
        triggers = []
        if isinstance(conf, (int, float)) and conf < _HARD_CONF:
            triggers.append('low_conf')
        if isinstance(margin, (int, float)) and margin < _HARD_MARGIN:
            triggers.append('low_margin')
        # 高置信零效果反查：post_delta 须是"在且空"（[]）——字段
        # 缺失（非录制/单快照/无基线）None 不触发，防正常高置信
        # 判定误伤；非空 diff=页面有变化，无错选嫌疑不触发
        _pd = result.get('post_delta')
        if (isinstance(conf, (int, float)) and conf >= _HARD_NO_EFFECT
                and isinstance(_pd, list) and not _pd):
            triggers.append('high_conf_no_effect')
        if not triggers:
            return
        meta = _ref_meta(session, state)
        probs_t = ((result.get('probs') or {}).get('target')) or {}

        def _prob(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        scored = {str(k): round(_prob(v), 4)
                  for k, v in probs_t.items()}
        opts = (state or {}).get('options') or []
        cands = []
        if opts:
            # schema_v=2：candidates 录全池（state['options'] 完整集，
            # recall_pool 扩池后上限 _HARD_POOL_CAP）——B_recall 桶
            # （目标在池但被 embedding top-k 裁掉）召回分析的数据前提。
            # 池序=ref 序保留；计分集内候选带 score，未计分池候选
            # score 缺省（下游按 schema_v 分流：缺省字段旧行=v1 top3）。
            seen = set()
            for o in opts[:_HARD_POOL_CAP]:
                rid = o.get('ref')
                c = {'ref': rid,
                     'name': o.get('name', ''),
                     'role': o.get('role', '')}
                s = scored.get(str(rid))
                if s is not None:
                    c['score'] = s
                cands.append(c)
                seen.add(str(rid))
            # 计分集内但不在池里的游离 ref（异常形态）按分降序附尾
            for k in sorted((k for k in scored if k not in seen),
                            key=lambda k: -_prob(probs_t[k])):
                m = meta.get(k) or {}
                cands.append({'ref': int(k) if k.isdigit() else k,
                              'name': m.get('name', ''),
                              'role': m.get('role', ''),
                              'score': scored[k]})
        else:
            # 无内嵌 state（state=None 录制形态）——退 v1 行为：
            # probs top3 计分候选，无全池可录
            for k, v in sorted(probs_t.items(),
                               key=lambda kv: -_prob(kv[1]))[:3]:
                m = meta.get(str(k)) or {}
                cands.append({'ref': int(k) if str(k).isdigit() else k,
                              'name': m.get('name', ''),
                              'role': m.get('role', ''),
                              'score': round(_prob(v), 4)})
            if not cands and result.get('ref') is not None:
                # 无 probs 引擎——引擎 pick 即唯一候选
                m = meta.get(str(result['ref'])) or {}
                cands.append({'ref': result['ref'],
                              'name': m.get('name', ''),
                              'role': m.get('role', ''), 'score': conf})
        row = {'ts': int(time.time()), 'session': session,
               'schema_v': _HARD_SCHEMA_V,
               'goal': goal, 'url': _hard_url(session, state),
               'engine': result.get('engine'),
               'action': result.get('action'),
               'engine_ref': result.get('ref'),
               'conf': round(float(conf), 4)
                       if isinstance(conf, (int, float)) else conf,
               'margin': margin,
               'margin_source': result.get('margin_source'),
               'tier': result.get('tier'),
               'unsure': bool(unsure),
               'triggers': triggers,
               'candidates': cands}
        if opts:
            # 原始候选总数（截断前）——options_total>len(candidates)
            # 即扩池仍被 _EMBED_POOL_MAX/本cap 截断，下游可知池外有遗
            row['options_total'] = (state.get('options_total')
                                    or len(opts))
        _hard_append(row)
    except Exception as e:
        try:
            from dp_cli.output import debug
            debug('hard-case 落盘异常（已忽略）', e)
        except Exception:
            pass


def log_escalate_feedback(session: str, goal: str, chosen_ref,
                          escalate: bool = True,
                          success: bool = False,
                          post_state: dict = None) -> bool:
    """escalate 反哺回写——decide 升级后上层自答成功的正例落盘。

    场景闭环：decide 返回 should_escalate=true（tier=low/refuse）
    → 上层（LLM/换策略）自行答出 chosen_ref 并执行成功
    （post_delta 非空为可选证据）→ 本函数把该正例回写
    hard_cases.jsonl：prov='escalate_feedback' +
    feedback='escalated' 标记（schema_v=2 契约内可选字段，
    review_hard_cases 透传）——把"升级后答对"从黑洞变成
    主动学习原料（上层答错的不写，由低置信落盘原通道覆盖）。

    fail-closed：success 非真或 escalate 非真一律不落——无执行
    成功证据的"答对"不成立；goal 空/chosen_ref 空同样拒收。
    DPX_HARD_CASES=0 总闸对本通道同效。返回 True=已落盘。
    """
    if not (escalate and success):
        return False
    if not isinstance(goal, str) or not goal.strip():
        return False
    if chosen_ref is None:
        return False
    if not hard_cases_enabled():
        return False
    row = {'ts': int(time.time()), 'session': session,
           'schema_v': _HARD_SCHEMA_V,
           'prov': 'escalate_feedback',      # 来源标记——与被动
                                            # 落盘行（无 prov）区分
           'feedback': 'escalated',          # 升级后答对语义标
           'goal': goal, 'url': _hard_url(session, None),
           'chosen_ref': chosen_ref,         # 上层升级后实选 ref
           'triggers': ['escalate_feedback'],
           'candidates': []}                 # 反馈形态无候选池快照
    if isinstance(post_state, dict) and post_state:
        row['post_state'] = post_state       # 可选执行后证据
    try:
        _hard_append(row)
        return True
    except Exception:
        return False                         # 旁路绝不炸调用方


def record(session: str, argv: list, refs: dict,
           goal: str = None, chosen_ref: int = None,
           unsure: bool = False, engine_ref: int = None,
           post_state: dict = None, pre_state: dict = None) -> None:
    """执行成功后异步采集一条。refs={rid:{role,name,locator}}。

    goal：推理分布的语义 query（decide 调用时的用户目标文本）；
    chosen_ref：decide 引擎选中的 ref（弱标注——引擎可能错，
    export 时标 src='decide' 供训练侧降权/过滤）。
    """
    if not enabled() or not argv or not refs:
        return
    cmd = argv[0]
    if cmd in _SKIP:
        return
    opts = [{'ref': int(k), 'role': v.get('role', ''),
             'name': v.get('name', '')}
            for k, v in refs.items() if str(k).isdigit()]
    if not opts:
        return
    entry = {'ts': int(time.time()), 'session': session,
             'state': {'refs': opts},
             'chosen': ' '.join(argv)}
    if goal:
        entry['goal'] = goal
    if chosen_ref is not None:
        entry['chosen_ref'] = chosen_ref
    if unsure:
        entry['unsure'] = True
    if engine_ref is not None:
        entry['engine_ref'] = engine_ref
    if pre_state:
        entry['pre_state'] = pre_state
    if post_state:
        # 执行后状态快照（url/ref 存活/页签名 diff）——让真实流量记录
        # 能补执行后证据过 verified_data.eligible()；无 diff 的行
        # 仍只是原料不冒充 verified。
        entry['post_state'] = post_state
        if pre_state:
            # 断言可执行性校验：post_delta 显式记录快照 diff 字段
            # 清单——非空=有执行后状态证据，[]=快照全同（动作疑似
            # 未生效），promote_recorded 见此标记直接不提升；
            # 缺/空 pre_state 无基线可判，不写 post_delta（空 dict
            # 是 _snap_state 失败形态，不能当"全字段变化"伪证据）。
            entry['post_delta'] = state_delta(pre_state, post_state)
    try:
        f = _file()
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open('a', encoding='utf-8') as fp:
            fp.write(json.dumps(entry, ensure_ascii=False) + '\n')
        _maybe_trim(f)
    except OSError:
        pass          # 采集失败不阻塞主链路（录制非关键路径）


def _record_decide_row(session: str, argv: list, result: dict,
                       code: int = 0, pre_state: dict = None) -> None:
    """三渠道公共录制入口——serve JSONL/REPL/daemon 共用同一判据。

    enabled() 总闸：未开 DPX_RECORD_DECIDE 时连 load_refs 文件 IO
    都不发生（daemon 热路径零额外成本）。code==0 或 decide 命令
    AGENT_UNSURE 才录——失败/异常路径不录；decide 拆
    goal/engine_ref/unsure；post_state 由 serve._snap_state
    采执行后快照（惰性 import 防环）。一切异常吞掉走
    debug——录制是旁路增强，绝不炸调用方主循环。
    """
    try:
        if not enabled() or not argv:
            return
        from dp_cli.commands import serve as _sv     # 惰性——防 import 环
        if _sv._IN_SNAP:                # _snap_state 内嵌 eval 不录
            return
        is_unsure = (argv[0] == 'decide' and len(argv) > 1
                     and isinstance(result, dict)
                     and result.get('code') == 'AGENT_UNSURE')
        if not (code == 0 or is_unsure):
            return
        from dp_cli.session import load_refs
        kw = {}
        _ps = _sv._snap_state(session)                # 执行后快照
        if _ps:
            kw['post_state'] = _ps
        if pre_state:
            kw['pre_state'] = pre_state               # 执行前快照
        if argv[0] == 'decide' and len(argv) > 1:
            # 引擎建议只存 engine_ref（无执行证据不作 chosen_ref）；
            # AGENT_UNSURE 标 unsure 供人工核验，detail JSON 解析
            # 失败保留 ref=None，不污染输出
            ref = None
            _d = None
            if code == 0:
                _d = result.get('data') if isinstance(result, dict) \
                    else None
                ref = (_d or {}).get('ref')
            else:
                try:
                    _d = json.loads(result.get('detail') or '')
                    ref = _d.get('ref')
                except Exception:
                    _d = None
            # conf 高置信盲区反查通道：双快照齐且页面零变化时把
            # post_delta 挂到 decide result 再过 hard-case 闸——
            # conf≥0.8（含 meta-rules deterministic=1.0 不经低置信
            # 闸的路径）+执行后零变化=疑似自信错选。仅在新触发
            # 前置成立时调用：快照缺一不挂字段（无 post_delta 不
            # 触发不误伤）；conf<0.8/有 diff 不重复过闸（低置信
            # 行 decide 返回路径已落盘，防纯重复行）。
            if isinstance(_d, dict) and pre_state and _ps:
                _pd = state_delta(pre_state, _ps)
                _c = _d.get('confidence')
                if (not _pd and isinstance(_c, (int, float))
                        and _c >= _HARD_NO_EFFECT):
                    _d = dict(_d)
                    _d['post_delta'] = _pd
                    _maybe_log_hard_case(_d, session, goal=argv[1],
                                         unsure=is_unsure)
            record(session, argv, load_refs(session) or {},
                   goal=argv[1], engine_ref=ref, unsure=is_unsure, **kw)
        else:
            record(session, argv, load_refs(session) or {}, **kw)
    except Exception as e:
        try:
            from dp_cli.output import debug
            debug('decide-train 录制异常（已忽略）', e)
        except Exception:
            pass


def _render(o: dict) -> str:
    return f"{o['role']} {o['name']}".strip()


# 元决策伪候选文本——与 bench_engines._ACT_CANDS/eval 侧同文，
# 模型学的是「goal 意图 → 动作伪候选」的排序而非元素
_ACT_TEXT = {
    'scroll_down': 'scroll down to reveal more content below',
    'scroll_up': 'scroll up back to the top of the page',
    'back': 'go back to the previous page',
    'wait': 'wait for the page to finish loading',
    'done': 'the goal is already complete, no action needed',
    'blocked': 'progress is blocked by a captcha or verification wall',
}


def export_pairs(path: Path = None) -> list:
    """jsonl → [{query, positive, negatives[]}]

    query = state 全部候选文本拼接（CrossEncoder 的 pair 输入形态：
    我们实际训的是 (goal≈chosen 语义, candidate 文本) ——故 query 用
    chosen 命令文本、positive 用被选元素描述更贴合 reranker 形态；
    但 chosen 是命令而非自然语言目标——此处采用双向导出：
    query=chosen 命令，positive=state 中含 ref 的元素描述。
    """
    f = path or _file()
    rows = []
    try:
        lines = f.read_text(encoding='utf-8').splitlines()
    except OSError:
        return rows
    import re
    ref_re = re.compile(r'@e\d+')
    for ln in lines:
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        opts = d.get('state', {}).get('refs') or []
        chosen = d.get('chosen', '')
        if not opts:
            continue
        pos_ref = query = None
        src = 'command'
        if d.get('meta_action'):    # 采集器 meta 样本：goal→动作伪候选
            act = d['meta_action']
            if act not in _ACT_TEXT:
                continue
            query = d.get('goal') or chosen
            neg = [_render(o) for o in opts]
            neg += [v for k, v in _ACT_TEXT.items() if k != act]
            rows.append({'query': query, 'positive': _ACT_TEXT[act],
                         'negatives': neg, 'src': 'meta'})
            continue
        if d.get('goal'):           # decide 路径：goal=推理分布 query
            query = d['goal']
            cr, er = d.get('chosen_ref'), d.get('engine_ref')
            if cr is not None and er is not None and cr == er:
                # 同源污染：chosen_ref==engine_ref 时"正例"实为引擎
                # 建议自身（非独立人工核验）——整行 skip 防自我强化偏差
                continue
            # 生产录制只写 engine_ref（serve/daemon 渠道无人工确认）——
            # chosen_ref（人工覆盖）优先，engine_ref（引擎建议）兜底
            pos_ref = cr if cr is not None else er
            src = 'decide'          # 弱标注——引擎 pick 非用户确认
        else:
            m = re.search(r'@e(\d+)', chosen)
            if m:
                pos_ref = int(m.group(1))
        if d.get('unsure'):
            # 置信度不足不证明引擎候选错误；只保留原始记录供人工核验。
            continue
        if not pos_ref:
            continue               # 无定位的命令无法定位正例——跳过
        pos = next((o for o in opts if o['ref'] == pos_ref), None)
        if not pos:
            continue
        if not query:
            # query 对齐推理分布：元素 name 是 goal 的最佳代理
            # （用户敲 click @e3 时的意图≈该元素的语义描述）；
            # 命令串兜底并 strip @eN 噪声 token。
            query = pos['name'] or ref_re.sub('', chosen).strip()
        neg = [_render(o) for o in opts if o['ref'] != pos_ref]
        rows.append({'query': query, 'positive': _render(pos),
                     'negatives': neg, 'src': src})
    return rows
