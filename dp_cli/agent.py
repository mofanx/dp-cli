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
import re
import subprocess
import sys
import time
from pathlib import Path

import click

from dp_cli.output import ok, error, debug, _dump, _write_err
from dp_cli.session import load_session, save_session, save_refs, load_refs
from dp_cli.commands._utils import session_option, _get_page

def _models_dir() -> Path:
    """agent 模型目录——惰性求值（import 期固化会让 HOME 隔离失效）。"""
    return Path.home() / '.dp_cli' / 'models'
_TOP_K = 20            # Julia-1 选项数上限（2-20）
_ACTIONS = ['click', 'type', 'select', 'scroll_up', 'scroll_down',
            'back', 'wait', 'done', 'blocked']
_CONF_FLOOR = 0.35     # 低于此置信 → AGENT_UNSURE
_STUCK_WINDOW = 3      # 连续 N 步哈希不变 → stuck
# refs epoch 签名纳入的交互属性子集——disabled/hidden/checked 等
# 属性级变化此前不重签，epoch 旧戳照样命中陈旧 refs
_SIG_PROPS = ('disabled', 'hidden', 'checked', 'expanded', 'selected',
              'pressed', 'required', 'readonly', 'focused', 'value',
              'valuetext', 'level', 'modal', 'multiselectable',
              'orientation', 'valuemin', 'valuemax')
_INTERACTIVE_ROLES = {
    'link', 'button', 'textbox', 'searchbox', 'combobox', 'listbox',
    'checkbox', 'radio', 'switch', 'tab', 'menuitem', 'option',
    'spinbutton', 'slider', 'treeitem',
}


# ── 控件表（Jev 格式）───────────────────────────────────────

def _page_state(page, session, max_opts=_TOP_K, recall_pool=False):
    """取 a11y 快照 → Jev 式 {state, options[{ref,role,name,current}]}。

    recall_pool=True（decide reranker 引擎）：原始可交互数超
    _EMBED_THRESHOLD 时不按 top_k 截断——全量候选交给 embedding
    召回粗排（治"opts≤top_k<阈值 recall 永不触发"缺陷）；
    池上限 _EMBED_POOL_MAX 防病理页面 encode 爆炸。
    """
    from dp_cli.snapshot.a11y import take_a11y_snapshot
    data = take_a11y_snapshot(page, max_depth=12)
    nodes = data.get('tree') or data.get('elements') or []
    if isinstance(nodes, dict):
        nodes = nodes.get('children', [])
    cands = [n for n in _walk(nodes)
             if (n.get('role') or '').lower() in _INTERACTIVE_ROLES]
    total = len(cands)        # 截断前原始 refs 数——recall 触发判据
    limit = max_opts
    if recall_pool and total > _EMBED_THRESHOLD:
        limit = max(max_opts, _EMBED_POOL_MAX)
    opts, refs = [], {}
    prop_bits = []            # 交互属性子集指纹——进 epoch sig
    for i, n in enumerate(cands[:limit], 1):
        role = (n.get('role') or '').lower()
        name = (n.get('name') or '').strip()[:120]
        cur = n.get('value')
        entry = {'ref': i, 'role': role, 'name': name}
        if cur not in (None, ''):
            entry['current'] = str(cur)[:80]
        opts.append(entry)
        _p = n.get('properties') or {}
        _bits = [f'{k}={_p[k]}' for k in _SIG_PROPS
                 if _p.get(k) not in (None, '')]
        if cur not in (None, ''):
            _bits.append(f'cur={cur}')
        prop_bits.append(','.join(_bits))
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
    # refs 版本戳：URL+控件签名（名字+交互属性子集+值）——
    # disabled/hidden/checked 等属性级变化同样重签，decide/后续
    # 动作据此检测过期
    sig = hashlib.sha1(
        f"{url}|{len(opts)}|{','.join(o['name'] for o in opts[:10])}"
        f"|{'|'.join(prop_bits)}"
        .encode()).hexdigest()[:12]
    if refs:
        save_refs(session, url, refs)
        sess = load_session(session) or {}
        sess['refs_epoch'] = {'sig': sig, 'url': url, 'at': time.time()}
        save_session(session, sess)
    return {'state': {'url': url, 'title': title, 'sig': sig},
            'options': opts, 'epoch': sig,
            'options_total': total,
            'truncated': total > len(opts)}


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


_RERANKER = None           # 惰性单例——首次调用才加载模型
_EMBED = None              # bge-embedding 召回模型（长页两级架构粗排）
_EMBED_MISS = False
_EMBED_THRESHOLD = int(os.environ.get('DPX_EMBED_THRESHOLD', '30'))
_EMBED_TOPK = int(os.environ.get('DPX_EMBED_TOPK', '8'))
_EMBED_POOL_MAX = int(os.environ.get('DPX_EMBED_POOL_MAX', '300'))


def _embed_model():
    """embedding 召回模型惰性加载——不可用时 None（降级全量打分）。

    进程内单例：同进程（serve/daemon/REPL）复用零成本；CLI 单发
    场景每进程首载 ~3.5-5s 属进程生命周期成本，可用
    DPX_NO_EMBED=1 显式禁用召回层。
    加载失败向 stderr 警告一次——静默降级会让召回层
    "存在但永不生效"无从察觉（实机验证 A1 教训）。
    """
    global _EMBED, _EMBED_MISS
    if _EMBED is None and not _EMBED_MISS:
        if os.environ.get('DPX_NO_EMBED'):
            return None                       # 用户显式关——不告警
        try:
            from sentence_transformers import SentenceTransformer
            _EMBED = SentenceTransformer(
                os.environ.get('DPX_EMBED_MODEL',
                               'BAAI/bge-small-zh-v1.5'))
        except Exception as e:
            _EMBED_MISS = True
            _write_err('dpx: embedding 召回模型加载失败，'
                       '召回层降级为全量打分'
                       '（DPX_NO_EMBED=1 可彻底禁用）：'
                       f'{type(e).__name__}: {e}\n')
    return _EMBED


def _recall_topk(goal, texts, opts, topk=None, total=None):
    """embedding 余弦召回 topk——候选超阈值时先粗排再过 CrossEncoder。

    total=截断前原始候选数（state['options_total']）——触发判定看
    原始数而非截断后 len(opts)，否则 opts≤top_k<阈值时永不触发。
    返回 (子集 opts, 子集 texts)；embedding 不可用返回原列表降级。"""
    topk = topk or _EMBED_TOPK
    if (total if total is not None else len(opts)) <= _EMBED_THRESHOLD:
        return opts, texts
    if len(opts) <= topk:
        return opts, texts                      # 池已≤topk，召回是恒等
    m = _embed_model()
    if m is None:
        return opts, texts                    # 降级：全量直排
    import numpy as np
    ve = np.asarray(m.encode([goal] + texts,
                             normalize_embeddings=True))
    sims = (ve[1:] @ ve[0]).tolist()
    idx = sorted(range(len(sims)), key=lambda i: -sims[i])[:topk]
    return [opts[i] for i in idx], [texts[i] for i in idx]
_RERANKER_FAM = None        # 'bge'|'minilm'——tier 封顶判据
_ORT_MOD = None            # onnxruntime 可用性缓存（False=不可用）


def _ort():
    """onnxruntime 懒加载——不可用记 False 不再重试。"""
    global _ORT_MOD
    if _ORT_MOD is None:
        try:
            import onnxruntime as o
            _ORT_MOD = o
        except ImportError:
            _ORT_MOD = False
    return _ORT_MOD or None


def _bge_dirs():
    """bge 候选目录——DPX_BGE_MODEL env 优先，其次微调权重，最后 zero-shot 基座。"""
    dirs = []
    env = os.environ.get('DPX_BGE_MODEL')
    if env:
        dirs.append(Path(env))
    dirs += [
        Path('/media/yan/Data/bge_v19_tuned'),      # v19 微调（eval-68 72.1%）
        Path.home() / '.dp_cli' / 'bge_model',      # 用户部署的 bge（ONNX/ST）
        Path('/media/yan/Data/bge_tuned'),          # 本项目实测 bge 权重
    ]
    return dirs


def _reranker_model():
    """加载 reranker——bge 为默认引擎，自训 MiniLM 为轻量备选。

    顺序：bge ONNX int8→bge ST（本地目录/hub）→MiniLM ONNX int8→MiniLM ST。
    bge zero-shot 73.7% 是实测上限；MiniLM 自训（22.7M/23MB INT8）
    保留为无 bge 环境的轻量降级路径。
    """
    global _RERANKER, _RERANKER_FAM
    if _RERANKER is not None:
        return _RERANKER
    tuned = Path.home() / '.dp_cli' / 'decide_model'
    ort = _ort()
    if ort:
        for d in _bge_dirs():
            for cand in ['model_int8.onnx', 'model.onnx']:
                f = d / 'onnx' / cand
                if f.exists():
                    try:
                        from transformers import AutoTokenizer
                        _RERANKER = ('ort', ort.InferenceSession(str(f)),
                                     AutoTokenizer.from_pretrained(str(d)))
                        return _RERANKER
                    except Exception:
                        break       # ort/transformers 损坏——继续降级
    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        CrossEncoder = None
    for d in _bge_dirs():
        if CrossEncoder and (d / 'config.json').exists():
            try:
                _RERANKER = ('st', CrossEncoder(str(d), max_length=512))
                _RERANKER_FAM = 'bge'
                return _RERANKER
            except Exception:
                continue
    if CrossEncoder:
        try:
            _RERANKER = ('st', CrossEncoder('BAAI/bge-reranker-base',
                                          max_length=512))
            _RERANKER_FAM = 'bge'
            return _RERANKER
        except Exception:
            pass                    # hub 不可达——落自训 MiniLM 兜底
    if ort:
        for cand in ['model_int8.onnx', 'model.onnx']:
            f = tuned / 'onnx' / cand
            if f.exists():
                try:
                    from transformers import AutoTokenizer
                    _RERANKER = ('ort', ort.InferenceSession(str(f)),
                                 AutoTokenizer.from_pretrained(str(tuned)))
                    _RERANKER_FAM = 'minilm'
                    return _RERANKER
                except Exception:
                    break
    if not CrossEncoder:
        return {'_error': '缺 sentence-transformers——'
                '`pip install dp-cli[agent]`'}
    _RERANKER = ('st', CrossEncoder(str(tuned), max_length=512))
    _RERANKER_FAM = 'minilm'
    return _RERANKER


def _engine_reranker(state, goal):
    """bge-reranker advisory——goal↔候选打分取 argmax。

    advisory-only：只产出建议+conf，绝不执行（执行权在上层/用户）。
    无 probs 的 done/stuck 问题不外包给模型——由确定性信号兜底。
    """
    m = _reranker_model()
    if isinstance(m, dict):
        return m
    opts = state['options']
    if not opts:
        return {'_error': '无可选候选'}
    import math
    q = _first_step(goal)                   # 多步 goal 截首步再打分
    texts = [f"{o['role']} {o['name']}" for o in opts]
    opts, texts = _recall_topk(                 # 长页两级：embedding 召回→CE 细排
        q, texts, opts, total=state.get('options_total'))
    if m[0] == 'ort':                       # ONNX 直跑 session
        _, sess, tok = m
        enc = tok([q] * len(texts), texts, return_tensors='np',
                  padding=True, truncation=True, max_length=128)
        scores = sess.run(['logits'], {k: enc[k] for k in
                          ['input_ids', 'attention_mask',
                           'token_type_ids']})[0].ravel().tolist()
    else:
        scores = m[1].predict([[q, t] for t in texts])
    mx = max(float(s) for s in scores)
    probs = [math.exp(float(s) - mx) for s in scores]
    tot = sum(probs)
    ranked = sorted(zip([o['ref'] for o in opts], probs), key=lambda x: -x[1])
    top_ref, top_p = ranked[0][0], ranked[0][1] / tot
    return {'target': top_ref,
            'probs': {'target': {str(k): round(v / tot, 4)
                                 for k, v in ranked}},
            'confidence': round(top_p, 4),
            'backend': m[0],
            'done': False, 'stuck': False}



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


_ROLE_ACTION = {'textbox': 'type', 'combobox': 'select', 'searchbox': 'type',
                'spinbutton': 'type', 'listbox': 'select'}


def _first_step(goal: str) -> str:
    """多步 goal 截首步——'先X再Y'/'X完再Y'/'X then Y' 只取第一步目标。

    reranker 只吃 (goal, 候选文本) 相似度，多步 goal 后半步的
    动作/目标词会拉偏打分（eval-120 x34/x41 miss 归因）：
    '先填手机号再点下一步'→'填手机号'。词界启发式，无 NLP 依赖；
    保守切分——形态不确定时返回原 goal 不截。
    """
    g = goal.strip()
    # '先' 前缀剥掉——'先填A再点B'/'先填A' 统一走首步段切分
    g2 = re.sub(r'^\s*先', '', g)
    # '再/然后/接着/随后/之后' 作分步分隔符，首步段 ≥2 字才截
    # （防'再见'类词内误切——前段不足 2 字不截）
    m = re.match(r'^\s*(.+?)(?:再|然后|接着|随后|之后)', g2)
    if m and len(m.group(1).strip('，,、 ')) >= 2:
        return m.group(1).strip('，,、 ')
    m = re.match(r'^\s*(.+?)\s+then\b', g, re.IGNORECASE)
    if m and len(m.group(1).strip('，,、 ')) >= 2:
        return m.group(1).strip('，,、 ')
    return g

# meta 决策=状态判断非语义排序——规则判定，不外包给概率模型
# （实测：语义学习对 done/blocked/scroll 零增益，meta 桶 42.9% 纹丝不动）
_DONE_GOAL = ('已完成', '完成', '确认', '验证', '成功', '检查', 'verify',
              'confirm', 'complete', 'done', 'shown')
_DONE_STATE = ('成功', '完成', 'welcome', '欢迎', 'results', '结果',
               'thank', 'success', 'complete', 'ok', '登出', '退出',
               'logged out', 'signed out', 'thank you')
_BLOCKED_KW = ('验证码', '人机', '机器人', 'captcha', 'verify you are',
               'robot', '安全验证', '盾', 'please verify', 'suspended',
               '被封', '封号', '拦截', 'security check')
# 表单字段含'验证码'≠拦截墙（登录页短信验证码输入框是正常可交互元素，
# eval-120 x34/x41 误判归因）——候选名判拦截时剔除'验证码'，仅 title 判
_BLOCKED_NAME_KW = tuple(k for k in _BLOCKED_KW if k != '验证码')
_BACK_KW = ('返回', '上一页', '后退', 'back', 'previous',
            '回到上一', '上个页面', '回列表', '回到搜索')
_WAIT_KW = ('等待', '加载', 'wait', 'load', 'settle', '稍等',
            '处理中', 'processing', '正在')
_SCROLL_UP_KW = ('顶部', '向上', 'top', 'scroll up')
_SCROLL_KW = ('下方', '页底', '更多', '滚动', '向下', 'scroll', 'more',
              'below', 'find', '找到', '查看全部')


def _kw_hit(kw, g, goal):
    """关键词命中——ASCII 词要求左词界，非 ASCII 走子串。

    '\\b' 左界根治 'load'⊂'download'/'ok'⊂'Tokyo' 类词内误命中
    （eval-120 n08 归因）；'loading'/'waited' 等形态后缀仍命中
    （右界不卡，保留词形变体召回）。
    """
    if kw.isascii():
        return re.search(r'\b' + re.escape(kw), g) is not None
    return kw in goal or kw in g


# 输入意图词——goal 含其一时 textbox/searchbox/spinbutton 目标才判 type
_TYPE_INTENT = ('输入', '填写', '填', '键入', '写', '搜', '搜索',
                '改成', 'type', 'fill', 'enter', 'search', 'write')


def _goal_action(goal, role):
    """role→action 映射 goal 感知——type 族角色无输入意图时降级 click。

    'reply to this thread' 的回复框是 focus 前置点击非 type（eval-120
    m12 归因）——裸 role 映射把"点击输入框"类 goal 错成 type。
    goal 含输入意图词（填/输入/写/搜/type/fill/…）仍判 type。
    """
    act = _ROLE_ACTION.get(role, 'click')
    if act == 'type':
        g = goal.lower()
        if not any(_kw_hit(k, g, goal) for k in _TYPE_INTENT):
            return 'click'
    return act


# 序数修饰'选项/option'→N 是 option 序号非元素序号（m26 归因）
_ORD_OPT_RE = re.compile(
    r'第\s*(?:\d+|[一二两三四五六七八九十]+)\s*(?:个|项)?\s*选项'
    r'|\b(?:\d+(?:st|nd|rd|th)|first|second|third|fourth|fifth|sixth|'
    r'seventh|eighth|ninth|tenth)\s+options?\b')


_TIER = {'probs': ('act', 'confirm', 'refuse'),
         'single_side': ('confirm', 'confirm', 'refuse'),
         'conf_fallback': ('confirm', 'confirm', 'refuse')}
_T_HI, _T_LO = 0.95, 0.40   # dev 集（38 disjoint）拟合：hit 簇 ≥0.90 / miss 簇密集 <0.40


def _conf_tier(conf: float, margin_source: str | None = None) -> str:
    """conf→act/confirm/refuse 三档——margin_source 契约对齐：

    probs        双侧真 margin——可给 act（高 conf 直采建议）
    single_side  单边 probs——上限 confirm（不当 act 证据）
    conf_fallback 静默降级——上限 confirm（conf 无校准保证）

    advisory 语义：act=高可信建议（仍不执行）/confirm=列 top-3 待确认/
    refuse=建议放弃。dev 拟合阈值 _T_HI/_T_LO，miss conf 也可达 1.0
    （softmax 过度自信）——tier 是表达档非正确率承诺。
    """
    src = margin_source if margin_source in _TIER else 'conf_fallback'
    a, c, r = _TIER[src]
    if conf >= _T_HI:
        return a
    return c if conf >= _T_LO else r


_CN_NUM = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
           '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
_ORD_WORDS = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4,
              'fifth': 5, 'sixth': 6, 'seventh': 7, 'eighth': 8,
              'ninth': 9, 'tenth': 10}


def _ordinal_n(g, goal):
    """goal 序数抽取——第N个/页/行/位/条、Nth、first..tenth、最后/last。
    返回 n（1-based）、-1（最后）、None（无序数信号）。"""
    m = re.search(r'第\s*(\d+|[一二两三四五六七八九十]+)\s*'
                  r'(?:个|页|行|位|条|项|列|名|节|章|张|步)?(?!次|遍|回)',
                  goal)
    if m:
        tok = m.group(1)
        if tok.isdigit():
            return int(tok)
        if len(tok) == 2 and tok[0] == '十':      # 十一..十九
            return 10 + _CN_NUM.get(tok[1], 0)
        return _CN_NUM.get(tok[-1]) or _CN_NUM.get(tok)
    if '最后' in goal or re.search(r'\blast\b', g):
        return -1
    mw = re.search(r'\b(\d+)(?:st|nd|rd|th)\b', g)
    if mw:
        return int(mw.group(1))
    for w, n in _ORD_WORDS.items():
        if re.search(rf'\b{w}\b', g):
            return n
    return None


def _meta_decide(state, goal):
    """meta 类决策确定性判定——命中返回 {action, deterministic}，否则 None。

    规则（与状态信号对应）：
      blocked  选项/title 含验证码特征词
      done     无候选 OR（goal 含完成意图 AND title/url 含完成态词）
      scroll   goal 含滚动/查找意图且目标名不在候选内
    """
    opts = state['options']
    title = (state['state'].get('title') or '').lower()
    g = goal.lower()
    names = ' '.join(o['name'].lower() for o in opts)
    if any(_kw_hit(k, names, names) for k in _BLOCKED_NAME_KW) or \
            any(_kw_hit(k, title, title) for k in _BLOCKED_KW):
        return {'action': 'blocked', 'deterministic': True,
                'reason': '验证码/人机特征命中'}
    if not opts:
        return {'action': 'done', 'deterministic': True,
                'reason': '无可交互候选'}
    if any(_kw_hit(k, g, goal) for k in _DONE_GOAL) and \
            any(_kw_hit(k, title, title) for k in _DONE_STATE):
        return {'action': 'done', 'deterministic': True,
                'reason': 'goal 完成意图+页面完成态'}
    # 序数定位确定性短路——'第N个/第N页/第N行/last' 直接映射 ref，
    # 模型只看 goal↔文本语义拿不到位置信号（eval-120 x-系 6 miss 归因）
    n = _ordinal_n(g, goal)
    if n is not None:
        # 序数修饰'选项/option'（'选下拉的第二个选项'）→ N 是 option
        # 序号非元素 ref——select 作用在 combobox/listbox 本体；
        # 候选无下拉元素时按普通元素序数处理（eval-120 m26 归因）
        if n > 0 and _ORD_OPT_RE.search(g):
            cb = next((o for o in opts
                       if o['role'] in ('combobox', 'listbox')), None)
            if cb is not None:
                return {'action': 'select', 'ref': cb['ref'],
                        'deterministic': True,
                        'reason': f'goal 含序数选项（第{n}个选项→下拉本体）'}
        ref = opts[-1]['ref'] if n == -1 else (n if n <= len(opts) else None)
        if ref is not None:
            is_type = any(_kw_hit(k, g, goal) for k in _TYPE_INTENT)
            act = 'type' if is_type else 'click'
            return {'action': act, 'ref': ref, 'deterministic': True,
                    'reason': f'goal 含序数定位（第{n if n > 0 else len(opts)}）'}
    if any(_kw_hit(k, g, goal) for k in _BACK_KW):
        return {'action': 'back', 'deterministic': True,
                'reason': 'goal 含返回意图'}
    if any(_kw_hit(k, g, goal) for k in _WAIT_KW):
        return {'action': 'wait', 'deterministic': True,
                'reason': 'goal 含等待意图'}
    # 滚动意图词若同时是候选元素名（'查看全部 12 条回复'按钮）→ 意图
    # 落在元素上非页面滚动，交 elem 打分不短路（eval-120 c10 归因）
    su_hit = [k for k in _SCROLL_UP_KW if _kw_hit(k, g, goal)]
    if su_hit and not any(_kw_hit(k, names, names) for k in su_hit):
        return {'action': 'scroll_up', 'deterministic': True,
                'reason': 'goal 含向上滚动意图'}
    sd_hit = [k for k in _SCROLL_KW if _kw_hit(k, g, goal)]
    if sd_hit and not any(_kw_hit(k, names, names) for k in sd_hit):
        return {'action': 'scroll_down', 'deterministic': True,
                'reason': 'goal 含向下滚动/查找意图'}
    return None





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
                resp['action'] = _goal_action(goal, role)
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
    if engine == 'reranker':
        resp = _engine_reranker(state, goal)
        if 'action' not in resp and 'target' in resp:
            try:
                t = int(resp['target'])
            except (TypeError, ValueError):
                t = -1
            role = next((o['role'] for o in state['options']
                         if o['ref'] == t), '')
            resp['action'] = _goal_action(goal, role)
        return resp, request
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
                  type=click.Choice(['external', 'julia', 'jev', 'reranker']),
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
        state = _page_state(page, session, max_opts=top_k,
                            recall_pool=(eng == 'reranker'))
        prev_sig = _state_sig(session)
        # meta 决策确定性短路——done/blocked/scroll 走规则非模型
        # （语义学习对状态决策零增益已实测；规则命中即出判定）
        meta = _meta_decide(state, goal)
        if meta:
            cur_sig = state['epoch']
            _record_hash(session, cur_sig)
            ok({'action': meta['action'], 'ref': meta.get('ref'),
                'confidence': 1.0,
                'deterministic': True, 'advisory': eng == 'reranker',
                'reason': meta['reason'], 'epoch': cur_sig,
                'options_count': len(state['options']),
                'truncated': state['truncated'], 'engine': 'meta-rules',
                'done': meta['action'] == 'done', 'stuck': False})
            return
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
        if eng == 'reranker':
            result['advisory'] = True   # 只建议不执行——契约写入响应
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
        if eng == 'reranker':
            # bge 实测 conf/margin 分布 hit/miss 完全重叠（eval-68），
            # 无校准证据可达 act——封顶 confirm；MiniLM 保留 act 档
            tier_src = msrc if resp.get('backend') != 'bge-st' \
                and msrc != 'conf_fallback' else msrc
            result['tier'] = _conf_tier(conf, tier_src)
            if _RERANKER_FAM == 'bge':
                # bge 引擎路径——conf/margin 分布重叠实测不可 act
                if result['tier'] == 'act':
                    result['tier'] = 'confirm'
        # bge softmax 分布集中 0.16-0.28（conf 无分离信号）——
        # bge 下 min_conf 门槛失效，unsure 只判 margin
        _conf_gate = min_conf if _RERANKER_FAM != 'bge' else 0.0
        _unsure = bool(not done and (conf and conf < _conf_gate
                                     or margin < min_margin))
        # hard-case 被动落盘：conf<0.5 或 top2 margin<0.1 →
        # ~/.dp_cli/hard_cases.jsonl（默认开，DPX_HARD_CASES=0 关；
        # 调试数据非训练承诺，与 decide_train 录制并行两份职责。
        # 挂在 decide 返回路径一处——CLI/serve/REPL/daemon 全渠道覆盖）
        try:
            from dp_cli.decide_train import _maybe_log_hard_case
            _maybe_log_hard_case(result, session, goal=goal,
                                 state=state, unsure=_unsure)
        except Exception:
            pass
        if _unsure:
            error(f'置信度 {conf:.2f} / margin {margin:.2f} 低于阈值——'
                  '候选不足或页面不适合当前决策模型',
                  code='AGENT_UNSURE',
                  detail=json.dumps(result, ensure_ascii=False))
            return
        ok(result)

    @cli.command('agent-setup')
    @session_option
    @click.option('--engine', 'eng',
                  type=click.Choice(['julia', 'jev', 'external', 'reranker']),
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

    @cli.command('decide-train-export')
    @click.option('-o', '--output', 'out', default=None,
                  help='导出路径（默认 stdout）')
    def decide_train_export(out):
        """导出录制数据为 CrossEncoder 训练集（query/positive/negatives）。

        数据源：~/.dp_cli/decide_train.jsonl（DPX_RECORD_DECIDE=1 录制）。
        """
        from dp_cli.decide_train import export_pairs, _file
        rows = export_pairs()
        if not rows:
            error('无可用训练对——先 DPX_RECORD_DECIDE=1 录制交互',
                  code='NO_DATA',
                  detail=str(_file()))
            return
        payload = json.dumps(rows, ensure_ascii=False, indent=2)
        if out:
            Path(out).write_text(payload, encoding='utf-8')
            ok({'pairs': len(rows), 'output': out},
               msg=f'{len(rows)} 条训练对已导出')
        else:
            click.echo(payload)
