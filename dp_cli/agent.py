# -*- coding:utf-8 -*-
"""agent 决策原语：agent-state / decide / agent-setup。

Jev 范式落地——决策模型只从**给定候选**里选，绝不生成 selector/JS：
  snapshot 控件表 → (engine) → {action, ref, probs, done, stuck}

引擎：
  external  默认——吐 JSON 请求，从 --answer/--answer-file/stdin 收答复，
            零依赖；与 julia1 predict / Jev API / LLM 同构协议
  julia     本地 Julia-1——julia1 predict 子进程调用（agent-setup 注册）
  jev       通用 OpenAI 兼容端点——DPX_JEV_URL/DPX_JEV_MODEL（+可选
            DPX_JEV_KEY/DPX_JEV_TIMEOUT）env 配置，chat/completions 协议

done：模型 done OR + 状态哈希变化检测（refs 版本戳）。
stuck：snapshot diff + churn 去噪 + N 步连续门槛——确定性信号，
        不外包给概率模型。
TYPE 动作只回 ref——value 由上层补，decide 不猜。
"""
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import OrderedDict
from pathlib import Path

import click

from dp_cli.output import ok, error, debug, _dump, _write_err
from dp_cli.session import load_session, save_session, save_refs, load_refs
from dp_cli.commands._utils import session_option, _get_page
from dp_cli.hints import adapter_hint, attach_hints

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
    # a11y._INTERACTIVE_ROLES 对齐——菜单态控件漏收即丢候选
    'menuitemcheckbox', 'menuitemradio',
}

# clickable_extras tag→role 映射——未覆盖 tag 统一 'clickable'
_EXTRA_TAG_ROLE = {'a': 'link', 'button': 'button', 'input': 'textbox',
                   'textarea': 'textbox', 'select': 'combobox',
                   'option': 'option', 'label': 'clickable'}


# ── 控件表（Jev 格式）───────────────────────────────────────

def _page_state(page, session, max_opts=_TOP_K, recall_pool=False):
    """取 a11y 快照 → Jev 式 {state, options[{ref,role,name,current}]}。

    recall_pool=True（decide reranker 引擎）：不按 top_k 截断——
    全量候选交给 embedding 召回粗排（>阈值）或 CE 直接细排
    （治"opts≤top_k<阈值 recall 永不触发"+total∈(top_k,阈值] 死区
    双缺陷）；池上限 _EMBED_POOL_MAX 防病理页面 encode 爆炸。

    召回覆盖三补（v26b 真机归因——63% miss 是候选召回层非排序）：
    - max_depth=None 全深度：depth=12 会把深层搜索框/提交按钮
      整子树裁掉（baidu #kw/#su 实测只在全深度树出现）
    - clickable_extras 并入候选：a11y 树漏收的真交互元素
      （div 频道 tab/div 按钮/隐藏 submit），探测白跑不合并即丢弃
    - 空 name DOM 回填：裸 input 无 label/placeholder 缺失时
      从 aria-label/label[for]/相邻文本/value 补名，末级再以
      name/id 机器属性拆词兜底（settings/zhihu/12306/gov 实测）
      ——语义寻址下 name='' 的候选等于不存在
    """
    from dp_cli.snapshot.a11y import take_a11y_snapshot
    data = take_a11y_snapshot(page, max_depth=None)
    nodes = data.get('tree') or data.get('elements') or []
    if isinstance(nodes, dict):
        nodes = nodes.get('children', [])
    cands = [n for n in _walk(nodes)
             if (n.get('role') or '').lower() in _INTERACTIVE_ROLES]
    # clickable_extras 并入候选（a11y 之后追加——ref 序号稳定，
    # extras 是二档线索置尾符合优先级）。无名 extras（纯图标/空 div）
    # 不收：语义寻址不可达还白占池位。
    for e in data.get('clickable_extras') or []:
        name = (e.get('label') or e.get('text') or '').strip()
        if name:
            cands.append(_extra_node(e, name))
    _fill_dom_names(page, cands)
    total = len(cands)        # 截断前原始 refs 数——recall 触发判据
    limit = max_opts
    if recall_pool:
        # 候选全量进池（上限 _EMBED_POOL_MAX）：total≤阈值时 CE 直接
        # 细排不需要 recall；total 在 (top_k, 阈值] 区间照旧截断会把
        # 尾部候选永久丢弃——旧 guard 只挡了 >阈值 一半场景
        limit = min(total, _EMBED_POOL_MAX)
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


def _extra_node(rec, name):
    """clickable_extras 记录 → a11y 式节点，与 _walk 产物同构进候选。"""
    tag = (rec.get('tag') or '').lower()
    return {'role': _EXTRA_TAG_ROLE.get(tag, 'clickable'),
            'name': name, 'value': '',
            'properties': {}, 'children': [],
            'locator': rec.get('locator'),
            'backendNodeId': rec.get('backendNodeId'),
            '_extra': True}


# 空 name 回填 JS：aria-label → label[for]/closest label →
# aria-labelledby → title/placeholder → 自有 innerText → 相邻文本节点 →
# input[submit/button] value → img alt → name/id 机器属性拆词
_DOM_NAME_JS = r"""
function () {
  const el = this;
  const pick = s => (s || '').replace(/\s+/g, ' ').trim();
  const attr = k => (el.getAttribute && el.getAttribute(k)) || '';
  let n = pick(attr('aria-label'));
  if (!n && el.id) {
    let l = null;
    try {
      l = el.ownerDocument.querySelector(
        'label[for="' + (window.CSS ? CSS.escape(el.id) : el.id) + '"]');
    } catch (e) {}
    if (l) n = pick(l.innerText);
  }
  if (!n && el.closest) {
    const l = el.closest('label');
    if (l) n = pick(l.innerText);
  }
  if (!n) {
    // aria-labelledby 指向的任意元素文本（antd/element-ui 常见
    // 旁挂 label 模式：<span id=x>出发地</span><input aria-labelledby=x>）
    for (const id of attr('aria-labelledby').split(/\s+/)) {
      const l = id && el.ownerDocument.getElementById(id);
      if (l) { n = pick(l.innerText); if (n) break; }
    }
  }
  if (!n) n = pick(attr('title')) || pick(attr('placeholder'));
  if (!n) n = pick(el.innerText);
  if (!n) {
    // 相邻文本：裸 input 无 label 场景（<input>Dark mode / 前缀文本<input>）
    const adj = (sib, fwd) => {
      for (let d = 0; sib && d < 4; d++) {
        if (sib.nodeType === 3) {
          const t = pick(sib.textContent);
          if (t) return t;
        } else if (sib.nodeType === 1) {
          if (/^(INPUT|SELECT|TEXTAREA|BUTTON|A|SCRIPT|STYLE)$/
              .test(sib.tagName)) break;
          const t = pick(sib.innerText);
          if (t) return t;
        }
        sib = fwd ? sib.nextSibling : sib.previousSibling;
      }
      return '';
    };
    n = adj(el.nextSibling, true) || adj(el.previousSibling, false);
    // 父级相邻：<div>出发地</div><div><input></div> 跨分支 label
    if (!n && el.parentNode)
      n = adj(el.parentNode.previousSibling, false)
          || adj(el.parentNode.nextSibling, true);
  }
  if (!n && el.tagName === 'INPUT' &&
      /^(submit|button|reset|checkbox|radio)$/i.test(el.type || ''))
    n = pick(el.value);
  if (!n && el.tagName === 'IMG') n = pick(el.alt);
  if (!n) {
    // 末级兜底：name/id 机器属性拆词——站点 a11y 全缺时给语义
    // 寻址留钩子（gov.cn 搜索框 name="headSearchword"，无 label/
    // placeholder/邻近文本）——camelCase/蛇形拆成可读词
    const raw = attr('name') || (el.id || '');
    if (raw.length >= 3 && /[A-Za-z一-鿿]/.test(raw)) {
      n = pick(raw.replace(/([a-z0-9])([A-Z])/g, '$1 $2')
                  .replace(/[_\-.\[\]#:]+/g, ' '));
    }
  }
  return (n || '').slice(0, 120);
}
"""


def _fill_dom_names(page, cands, max_nodes=25):
    """空 name 候选 DOM 回填——a11y name 缺失时从 DOM 侧补名。

    每节点两次 CDP（resolveNode→callFunctionOn），仅处理有空 name 且
    有 backendNodeId 的 a11y 节点，封顶 max_nodes 防病态页拖慢
    snapshot；extras 自带 name 不参与。全部失败静默——补名是增强
    非必需，回填不到就保持原状。
    """
    empties = [n for n in cands if not (n.get('name') or '').strip()
               and n.get('backendNodeId') and not n.get('_extra')]
    for n in empties[:max_nodes]:
        try:
            r = page.run_cdp('DOM.resolveNode',
                             backendNodeId=n['backendNodeId'])
            oid = (r.get('object') or {}).get('objectId')
            if not oid:
                continue
            res = page.run_cdp('Runtime.callFunctionOn',
                               objectId=oid,
                               functionDeclaration=_DOM_NAME_JS,
                               returnByValue=True)
            v = ((res.get('result') or {}).get('value') or '').strip()
            if v:
                n['name'] = v
        except Exception:
            continue


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


_LEXICAL_K = 3           # lexical 补缺并入 CE 的上限（union k+≤3）
_LEXICAL_FLOOR = 0.5     # lexical score 门控——低于此算噪声不并入


def _goal_tokens(goal):
    """goal 分词——ASCII 词与 CJK 连续段各成一个 token（中英通用）。"""
    return re.findall(r'[a-z0-9_]+|[一-鿿]+', goal.lower())


def _lexical_top(opts, goal, k=_LEXICAL_K):
    """lexical 召回补缺——goal↔候选 name 字面匹配 top-k（stdlib 零依赖）。

    score = max(token 覆盖度, name⊂goal 直含, SequenceMatcher 相似度)：
      - 覆盖度：goal 分词后出现在 name 里的 token 占比
        （'click submit order'→'Submit Order' 覆盖 2/3）
      - 直含：候选 name 是 goal 子串（'提交订单'⊂'点提交订单'，
        限 len≥2 防单字名误中）
      - 相似度：difflib 编辑相似度兜底（'提交订单'↔'点击提交订单按钮'）
    返回 [(idx, score)] score≥_LEXICAL_FLOOR 的 top-k，按分降序。
    """
    import difflib
    g = goal.lower().strip()
    toks = _goal_tokens(goal)
    if not g or not toks:
        return []
    scored = []
    for i, o in enumerate(opts):
        n = (o.get('name') or '').lower().strip()
        if not n:
            continue
        cov = sum(1 for t in toks if t in n) / len(toks)
        s = max(cov, difflib.SequenceMatcher(None, g, n).ratio())
        if len(n) >= 2 and n in g:
            s = max(s, 1.0)
        if s >= _LEXICAL_FLOOR:
            scored.append((i, s))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:k]


def _recall_topk(goal, texts, opts, topk=None, total=None):
    """embedding 余弦召回 topk ∪ lexical top3——粗排后过 CrossEncoder。

    total=截断前原始候选数（state['options_total']）——触发判定看
    原始数而非截断后 len(opts)，否则 opts≤top_k<阈值时永不触发。
    union 形态：embed kept_idx ∪ lexical top3 idx 去重——字面重名/
    近名候选 embed 可能漏召（语义空间重名簇挤位），lexical 层
    用 token 覆盖/子串/编辑相似度补进 CE（DPX_NO_LEXICAL=1 关）。
    embedding 不可用时 lexical 独走——降级下仍是免费召回层；
    lexical 也无命中才退回全量直排。返回 (子集 opts, 子集 texts)。"""
    topk = topk or _EMBED_TOPK
    if (total if total is not None else len(opts)) <= _EMBED_THRESHOLD:
        return opts, texts
    if len(opts) <= topk:
        return opts, texts                      # 池已≤topk，召回是恒等
    keep = []
    m = _embed_model()
    if m is not None:
        import numpy as np
        ve = np.asarray(m.encode([goal] + texts,
                                 normalize_embeddings=True))
        sims = (ve[1:] @ ve[0]).tolist()
        keep = sorted(range(len(sims)), key=lambda i: -sims[i])[:topk]
    if not os.environ.get('DPX_NO_LEXICAL'):
        for li, _s in _lexical_top(opts, goal):
            if li not in keep:                  # 去重——embed 已留的不重入
                keep.append(li)
    if not keep:
        return opts, texts                      # 双路皆空——全量直排兜底
    return [opts[i] for i in keep], [texts[i] for i in keep]
_RERANKER_FAM = None        # 'bge'|'minilm'——tier 封顶判据
_RERANKER_SRC = None        # ort 实际加载的 onnx 文件名（model-status 回显）
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
    """bge 候选目录——DPX_BGE_MODEL env 最高，其次用户部署位，最后开发机遗留路径。

    `~/.dp_cli/bge_model` 是 `dpx model pull` 的官方落位（v24+）——必须排在
    开发机遗留路径之前，否则用户 pull 完实际加载的还是旧微调权重。
    """
    dirs = []
    env = os.environ.get('DPX_BGE_MODEL')
    if env:
        dirs.append(Path(env))
    dirs += [
        Path.home() / '.dp_cli' / 'bge_model',      # 用户部署位（model pull 落点）
        Path('/media/yan/Data/bge_v28_tuned'),      # 开发机 v28 微调
        Path('/media/yan/Data/bge_v24_tuned'),      # 开发机 v24 微调
        Path('/media/yan/Data/bge_v19_tuned'),      # 开发机 v19 遗留
        Path('/media/yan/Data/bge_tuned'),          # 开发机更早实测权重
    ]
    return dirs


def _reranker_model():
    """加载 reranker——bge 为默认引擎，自训 MiniLM 为轻量备选。

    顺序：bge ONNX int8→bge ST（本地目录/hub）→MiniLM ONNX int8→MiniLM ST。
    bge zero-shot 73.7% 是实测上限；MiniLM 自训（22.7M/23MB INT8）
    保留为无 bge 环境的轻量降级路径。
    """
    global _RERANKER, _RERANKER_FAM, _RERANKER_SRC
    if _RERANKER is not None:
        return _RERANKER
    tuned = Path.home() / '.dp_cli' / 'decide_model'
    # DPX_BGE_ONNX=0 逃生开关——关 ORT 路径直走 ST（对照 DPX_NO_LEXICAL 惯例）
    ort = None if os.environ.get('DPX_BGE_ONNX', '1') == '0' else _ort()
    if ort:
        for d in _bge_dirs():
            for cand in ['model_int8.onnx', 'model.onnx']:
                f = d / 'onnx' / cand
                if f.exists():
                    try:
                        from transformers import AutoTokenizer
                        _RERANKER = ('ort', ort.InferenceSession(
                                         str(f),
                                         providers=['CPUExecutionProvider']),
                                     AutoTokenizer.from_pretrained(str(d)))
                        _RERANKER_FAM = 'bge'
                        _RERANKER_SRC = cand
                        return _RERANKER
                    except Exception:
                        continue    # int8 损坏→fp32→下个目录→ST 逐级降级
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
                    _RERANKER = ('ort', ort.InferenceSession(
                                     str(f),
                                     providers=['CPUExecutionProvider']),
                                 AutoTokenizer.from_pretrained(str(tuned)))
                    _RERANKER_FAM = 'minilm'
                    _RERANKER_SRC = cand
                    return _RERANKER
                except Exception:
                    continue            # int8 损坏→fp32→ST 兜底
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
        # 按 session 实际输入名喂参——XLM-R tokenizer 不出 token_type_ids
        # （硬编码会 KeyError）；BERT 系含 token_type_ids 的模型自动兼容
        feed = {i.name: enc[i.name] for i in sess.get_inputs()}
        scores = sess.run(['logits'], feed)[0].ravel().tolist()
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


_JEV_SYS_PROMPT = (
    '你是浏览器自动化决策引擎。用户消息是 JSON：'
    'state（页面 URL/Title）+ questions（action/target/done/stuck 四问，'
    'criteria 是候选表，键即元素编号）。只输出一个 JSON 对象，schema：'
    '{"action":"click|type|select|scroll_up|scroll_down|back|wait|done'
    '|blocked","target":<元素编号 int 或 null>,"done":<bool>,'
    '"stuck":<bool>,"confidence":<0~1 float>}——'
    'TYPE 动作同样只回 target，value 由上层补。不要输出任何解释。')


def _jev_parse_content(content):
    """模型答复→dict——剥离 ```json 围栏/前后噪声，取首个完整 JSON 对象。"""
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return None
    s = content.strip()
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r'\{.*\}', s, re.DOTALL)
    if m:
        try:
            v = json.loads(m.group(0))
            return v if isinstance(v, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _engine_jev(request):
    """通用 OpenAI chat/completions 兼容端点——DPX_JEV_* env 配置。

    env：
      DPX_JEV_URL      chat/completions 端点 URL（必填，未配→_error）
      DPX_JEV_KEY      Bearer key（可选——本地端点可空）
      DPX_JEV_MODEL    model 字段（必填）
      DPX_JEV_TIMEOUT  秒，默认 30
    request 即 _ask_questions 的三问 JSON——原样进 user message，要求
    模型回同构 {action,target,done,stuck,confidence}；'ref' 键兼容
    归一为 'target'。全部异常收敛 _error，不裸 raise（JSON 契约）。
    """
    url = (os.environ.get('DPX_JEV_URL') or '').strip()
    if not url:
        return {'_error': '缺 DPX_JEV_URL 环境变量——'
                '配置 OpenAI 兼容 chat/completions 端点后重试'}
    model = (os.environ.get('DPX_JEV_MODEL') or '').strip()
    if not model:
        return {'_error': '缺 DPX_JEV_MODEL 环境变量'}
    try:
        timeout = float(os.environ.get('DPX_JEV_TIMEOUT', '30'))
    except ValueError:
        timeout = 30.0
    import socket
    import urllib.error
    import urllib.request
    headers = {'Content-Type': 'application/json'}
    key = (os.environ.get('DPX_JEV_KEY') or '').strip()
    if key:
        headers['Authorization'] = f'Bearer {key}'
    body = {'model': model, 'temperature': 0,
            'messages': [
                {'role': 'system', 'content': _JEV_SYS_PROMPT},
                {'role': 'user',
                 'content': json.dumps(request, ensure_ascii=False)}]}
    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode(errors='replace')[:200]
        except Exception:
            pass
        return {'_error': f'Jev API HTTP {e.code}: {detail}'}
    except urllib.error.URLError as e:
        reason = getattr(e, 'reason', e)
        if isinstance(reason, (TimeoutError, socket.timeout)) or \
                'timed out' in str(reason).lower():
            return {'_error': f'Jev API 超时（{timeout:g}s）'}
        return {'_error': f'Jev API 连接失败: {reason}'}
    except TimeoutError:
        return {'_error': f'Jev API 超时（{timeout:g}s）'}
    except json.JSONDecodeError as e:
        return {'_error': f'Jev API 响应非 JSON: {e}'}
    except OSError as e:
        return {'_error': f'Jev API 连接失败: {e}'}
    try:
        content = payload['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError) as e:
        return {'_error': f'Jev API 响应缺 choices/message: {e}'}
    resp = _jev_parse_content(content)
    if resp is None:
        return {'_error': 'Jev 答复非 JSON——模型未按 schema 输出'}
    if 'target' not in resp and 'ref' in resp:
        resp['target'] = resp['ref']
    return resp


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
    # '不是X的' 中置否定定语剥离——'选不是"取消"的那个' 的
    # '不是"取消"的' 是修饰定语非正选目标，剥掉整段只留正选
    # （eval-120 m30 miss 归因：'取消'字面量把 reranker 拉向
    # 否定对象）。剥后正选段不足 2 字不动（'我不是故意的'类
    # 防误伤）；剥过则尾部返回 g2 形态（否则仍回原 goal）。
    _ns = re.sub(r'不是[^，,、。；]+?的', '', g2)
    _ns_hit = _ns != g2 and len(_ns.strip('，,、 ')) >= 2
    if _ns_hit:
        g2 = _ns
    # 否定前置段剥离——'别点A点B'/'不要A，去B'/'不是A是B'/'不要A要B'
    # 的 A 段是反目标，不剥则 reranker 被否定词的对象拉偏
    # （eval-120 m29 '别点删除点归档'→误中 Delete 归因）；剥离后
    # 只留 B 段打分。排除集含动作动词字保证剥离段至少含一个非
    # 动词目标词（'禁止点击这里'类纯否定不剥）；lookahead 含
    # '是/要' 覆盖 '不是X是Y'/'不要X要Y' 对举句式，剥后连接字
    # '是/要' 一并清掉只留正选。
    m = re.match(r'^\s*(?:别|不要|不用|不需要|不必|无需|请勿|千万别|禁止'
                 r'|不是)'
                 r'\s*(?:点击|点|按|选|击|碰|删|勾|填|输|进|去)?'
                 r'[^点按选击填输去，,、。；]+?[，,、]?\s*'
                 r'(?=[点按选击填输去是要])', g2)
    if m:
        rest = g2[m.end():].strip('，,、 是要')
        if len(rest) >= 2:
            return rest
    # '再/然后/接着/随后/之后' 作分步分隔符，首步段 ≥2 字才截
    # （防'再见'类词内误切——前段不足 2 字不截）
    m = re.match(r'^\s*(.+?)(?:再|然后|接着|随后|之后)', g2)
    if m and len(m.group(1).strip('，,、 ')) >= 2:
        return m.group(1).strip('，,、 ')
    m = re.match(r'^\s*(.+?)\s+then\b', g, re.IGNORECASE)
    if m and len(m.group(1).strip('，,、 ')) >= 2:
        return m.group(1).strip('，,、 ')
    # '不是X的' 剥离命中但无其他切分规则——返回净化后的 g2
    # （'选不是"取消"的那个'→'选那个'）；未剥过仍回原 goal
    return g2 if _ns_hit else g

# meta 决策=状态判断非语义排序——规则判定，不外包给概率模型
# （实测：语义学习对 done/blocked/scroll 零增益，meta 桶 42.9% 纹丝不动）
_DONE_GOAL = ('已完成', '完成', '确认', '验证', '成功', '检查', 'verify',
              'confirm', 'complete', 'done', 'shown')
_DONE_STATE = ('成功', '完成', 'welcome', '欢迎', 'results', '结果',
               'thank', 'success', 'complete', 'ok', '登出', '退出',
               'logged out', 'signed out', 'thank you')
# blocked 判据收窄（P2 backlog blocked 全池误判归因——资讯站候选名/标题
# 含'人形机器人/拦截/无人机/盾'类内容词致整页短路，ithome/cnblogs/csdn/HN
# 必中且连 hard_case 采集都掐掉）：只保留真拦截墙信号——验证挑战短语与
# 账号封禁态，剔除可被普通内容命中的'人机/机器人/robot/拦截/盾'
# （'人机'子串还误中'无人机'、'盾'误中'矛盾'）
_BLOCKED_KW = ('验证码', '人机验证', 'captcha', 'verify you are',
               '安全验证', 'please verify', 'suspended', '被封',
               '封号', 'security check')
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

# 动作词族——goal 词与候选 name 的同族映射（词界信号非语义相似）。
# 族内词互视为同义：goal 命中任一词、候选名命中任一词即同族。
# 只收高确定性动作域（误族=误选元素，族词歧义大的一律不收）
_ACTION_FAMILY = ({'结算', '结账', '买单', '付款', '支付', '下单',
                   '收银', 'checkout'},
                  )
# 字号符号按钮——goal 调大/调小意图 ↔ name 规范符号形态。
# 只收 'a+'/'a-'/'+'/'-' 等 canonical 形态——'C++'/'100+' 类
# 内容名不误中（eval-120 m14：'A-'/'A+' CE 分差 0.0004 不可分，
# 语义在符号上模型学不到）
_FONT_UP_KW = ('调大', '放大', '加大', '变大', '增大',
               'larger', 'bigger', 'zoom in')
_FONT_DN_KW = ('调小', '缩小', '变小', '减小',
               'smaller', 'zoom out')
_FONT_PLUS_NAME = {'a+', '+', '＋'}
_FONT_MINUS_NAME = {'a-', '-', '−', '－'}


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


def _act_tier(conf: float, margin_source: str | None = None) -> str:
    """conf→act/confirm/refuse 三档——margin_source 契约对齐：

    probs        双侧真 margin——可给 act（高 conf 直采建议）
    single_side  单边 probs——上限 confirm（不当 act 证据）
    conf_fallback 静默降级——上限 confirm（conf 无校准保证）

    advisory 语义：act=高可信建议（仍不执行）/confirm=列 top-3 待确认/
    refuse=建议放弃（置信不足该升级——调用点同步压
    confidence_tier=low、should_escalate=true）。dev 拟合阈值
    _T_HI/_T_LO，miss conf 也可达 1.0（softmax 过度自信）——
    tier 是表达档非正确率承诺。
    """
    src = margin_source if margin_source in _TIER else 'conf_fallback'
    a, c, r = _TIER[src]
    if conf >= _T_HI:
        return a
    return c if conf >= _T_LO else r


# confidence_tier 分档阈值——与 _CONF_FLOOR/min_margin 校准值同族：
# high 双信号齐（conf/margin 分布重叠实测单一信号不可信），
# low = unsure 门控命中或 conf 破底，其余 medium。
_CT_HI_CONF = 0.70
_CT_HI_MARGIN = 0.10


def _conf_tier(conf: float, margin: float, unsure: bool = False,
               conf_floor: float = _CONF_FLOOR) -> str:
    """conf+margin→high/medium/low 结构化置信档（decide 响应字段）。

    high    conf≥0.70 且 margin≥0.10
    low     unsure 门控命中 OR conf<conf_floor（默认 _CONF_FLOOR=0.35；
            调用方传 _conf_gate——bge conf 分布无区分度时 floor=0，
            low 档只由 unsure/margin 触发，不误标整页）
    medium  其余——可分但留疑，建议上层确认

    tier 是表达档非正确率承诺（softmax 过度自信实测）。
    """
    if unsure or conf < conf_floor:
        return 'low'
    if conf >= _CT_HI_CONF and margin >= _CT_HI_MARGIN:
        return 'high'
    return 'medium'


def _alternatives(state, goal, probs_t, top_ref, k=2):
    """ranked 第2-3名备选 [{ref,name,score}]——AGENT_UNSURE 拒答场景
    备选最有用，agent 可携候选集 escalate。

    排序信号两路：probs['target'] 有→概率降序（score=归一概率）；
    无 probs（jev/external 仅回 target）→_lexical_top 字面相似度
    序兜底（score=相似度分）；再无信号空列表如实返回。
    top1（引擎选中 ref）恒排除。
    """
    opts = state.get('options') or []
    by_ref = {str(o['ref']): o for o in opts}

    def _p(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    alts = []
    if probs_t:
        ranked = sorted(probs_t.items(), key=lambda kv: -_p(kv[1]))
        for rk, sc in ranked:
            if str(rk) == str(top_ref):
                continue
            o = by_ref.get(str(rk)) or {}
            alts.append({'ref': int(rk) if str(rk).isdigit() else rk,
                         'name': o.get('name', ''),
                         'score': round(_p(sc), 4)})
            if len(alts) >= k:
                break
        return alts
    for i, s in _lexical_top(opts, goal, k + 1):
        o = opts[i]
        if str(o['ref']) == str(top_ref):
            continue
        alts.append({'ref': o['ref'], 'name': o.get('name', ''),
                     'score': round(float(s), 4)})
        if len(alts) >= k:
            break
    return alts


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
    # 口语尾序数'地址二/方案三'——名词+句尾中文数词即第N个
    # （eval-120 n05 '选择收货地址二' 归因）；lookbehind 防
    # '十二/初三/星期一/礼拜五' 类数词词内/时间词误判
    # （'一/两' 量词歧义大不收——'统一/一两' 会假阳）
    m = re.search(r'(?<![\d十百千初期周拜第])([二三四五六七八九])\s*$', goal)
    if m:
        return _CN_NUM[m.group(1)]
    # 口语中置序数'地址二设成X/方案三改成Y'——数词+动作动词字的中段
    # 形态（eval-120 m11 '把地址二设成收货地址' 归因：尾序数 \s*$ 锚
    # 只收句尾，漏中段）。动作动词 lookahead 守词界防'二话不说/
    # 二维码/三好学生'类数词词内误中——后接非动作字不判序数
    m = re.search(r'(?<![\d十百千初期周拜第])([二三四五六七八九])'
                  r'(?=[设选定改调换作为当])', goal)
    if m:
        return _CN_NUM[m.group(1)]
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
      ordinal  goal 含序数定位（第N个/尾数词/中置数词+动词）→ 直解 ref
      scroll   goal 含滚动/查找意图且目标名不在候选内
      元素直解 动作词族唯一命中 / 字号符号按钮唯一命中
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
    # ── 确定性元素直解（词界/符号信号，非语义排序）──
    # goal 侧统一吃 _first_step 截断形态——'别点A点B' 的 A 段否定词
    # 不得命中族词（'不要点结算点取消' 不剥会误选结算按钮）
    g_fs = _first_step(goal).lower()
    # 动作词族唯一命中——'结算购物车'→'去结算'：goal 与 name 字面
    # 同族是强词界信号，而 'Continue shopping' 只是语义近把模型
    # 拉偏（eval-120 m09 归因）。唯一性是安全带——同族多候选=
    # 真歧义，留模型排序不短路
    for fam in _ACTION_FAMILY:
        if not any(_kw_hit(k, g_fs, g_fs) for k in fam):
            continue
        hits = [o for o in opts
                if any(_kw_hit(k, (o.get('name') or '').lower(),
                               o.get('name') or '') for k in fam)]
        if len(hits) == 1:
            o = hits[0]
            return {'action': _goal_action(goal, o['role']),
                    'ref': o['ref'], 'deterministic': True,
                    'reason': f'goal 动作词族唯一命中（{o["name"]}）'}
    # 字号符号按钮——'调大一号'类 goal 语义全在 +/- 符号上，name
    # 字面为零模型学不到（eval-120 m14：'A-'/'A+' 分差 0.0004）。
    # 只收规范形态防 'C++'/'100+' 内容名误中；唯一性同上
    for kws, syms in ((_FONT_UP_KW, _FONT_PLUS_NAME),
                      (_FONT_DN_KW, _FONT_MINUS_NAME)):
        if not any(_kw_hit(k, g_fs, g_fs) for k in kws):
            continue
        hits = [o for o in opts
                if (o.get('name') or '').strip().lower() in syms]
        if len(hits) == 1:
            return {'action': 'click', 'ref': hits[0]['ref'],
                    'deterministic': True,
                    'reason': 'goal 字号意图+规范符号按钮唯一命中'}
    return None





# ── decide 决策缓存（reranker CE 重打分免重复）─────────────────
# daemon/serve 常驻下，同 (session,url,epoch,goal) 二次 decide 免重编码：
# CE 打分是 (goal,candidates) 的纯函数，refs_epoch 即页面 DOM 签名——
# 页面变即 epoch 变、key 不同天然失效（守卫免写）；session 名进 key
# 天然隔离。缓存的是引擎 resp（决策级：含 action 映射+probs/conf），
# margin/tier/unsure/done/stuck 仍逐次现算——--min-conf/--min-margin/
# --cascade 等输出参数不进 key 也始终生效。
# 只适用 reranker：external 走 stdin 答复、jev 是远端 API，均不可缓存。
# 如实边界：CLI 单发每次新进程永不命中——只有 daemon/serve/REPL
# 常驻进程内缓存才生效。
_DECIDE_CACHE_MAX = int(os.environ.get('DPX_DECIDE_CACHE_MAX', '128'))
_DECIDE_CACHE = OrderedDict()


def _decide_cache_key(session, state, goal, engine):
    """缓存键 (session,url,epoch,goal,engine)——页面/session 变即失效。"""
    return (session, (state.get('state') or {}).get('url') or '',
            state.get('epoch') or '', goal, engine)


def _decide_cache_get(key):
    """LRU 命中返回 resp 深拷贝——DPX_NO_CACHE=1 整层关闭。"""
    if os.environ.get('DPX_NO_CACHE'):
        return None
    resp = _DECIDE_CACHE.get(key)
    if resp is None:
        return None
    _DECIDE_CACHE.move_to_end(key)
    return copy.deepcopy(resp)


def _decide_cache_put(key, resp):
    """LRU 写入+超上限驱逐最旧——_error 答复不入缓存。"""
    if os.environ.get('DPX_NO_CACHE') or '_error' in resp:
        return
    _DECIDE_CACHE[key] = copy.deepcopy(resp)
    _DECIDE_CACHE.move_to_end(key)
    while len(_DECIDE_CACHE) > _DECIDE_CACHE_MAX:
        _DECIDE_CACHE.popitem(last=False)


def _ask_questions(state, goal, engine, session, answer, answer_file,
                   no_cache=False):
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
        resp = _engine_jev(request)
        if 'action' not in resp and 'target' in resp:
            try:
                t = int(resp['target'])
            except (TypeError, ValueError):
                t = -1
            role = next((o['role'] for o in state['options']
                         if o['ref'] == t), '')
            resp['action'] = _goal_action(goal, role)
        return resp, request
    if engine == 'reranker':
        ck = _decide_cache_key(session, state, goal, engine)
        resp = None if no_cache else _decide_cache_get(ck)
        if resp is not None:
            resp['_cache_hit'] = True        # decide 翻译成 cached:true
            return resp, request
        resp = _engine_reranker(state, goal)
        if 'action' not in resp and 'target' in resp:
            try:
                t = int(resp['target'])
            except (TypeError, ValueError):
                t = -1
            role = next((o['role'] for o in state['options']
                         if o['ref'] == t), '')
            resp['action'] = _goal_action(goal, role)
        if not no_cache:
            _decide_cache_put(ck, resp)
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
    @click.option('--min-margin', default=0.005, show_default=True,
                  help='top1-top2 margin 阈值（歧义判定，比绝对置信可靠；'
                       'eval-120 校准：可拦 miss margin≤0.0028，'
                       '0.005 全拦且假拒 -74% vs 旧值 0.15）')
    @click.option('--cascade', is_flag=True,
                  help='级联模式：Julia 粗筛 top-3 候选集给上层终裁')
    @click.option('--no-cache', is_flag=True,
                  help='跳过决策缓存（reranker 引擎；DPX_NO_CACHE=1 等效）')
    @click.option('--top-k', default=_TOP_K, show_default=True)
    def decide(goal, session, engine, answer, answer_file,
               min_conf, min_margin, cascade, no_cache, top_k):
        """【experimental 未验证准确率】单步决策：扇出三问 → {op,ref,probs}。

        ⚠ 波次 0 实测：Julia-1 对浏览器控件任务准确率 ~40%，未达立项
        阈值（≥85% LLM 基线）——本命令保留用于外部引擎/benchmark，
        不建议生产使用。

        \b
        引擎:
          external  吐请求收答复（--answer/--answer-file/stdin），零依赖
          julia     本地 Julia-1（先 dpx agent-setup --engine julia）
          jev       OpenAI 兼容端点（需 env：DPX_JEV_URL+DPX_JEV_MODEL，
                    可选 DPX_JEV_KEY/DPX_JEV_TIMEOUT）

        \b
        示例:
          dpx decide "搜索 Python" --engine external \\
              --answer '{"action":"type","target":4,"done":false}'
          dpx decide "点登录" --engine julia

        \b
        结构化置信度（result 字段）:
          hints.adapter_hint  当前页 host 命中 adapter 注册表时给出
                              {name,method,params_keys}——可考虑
                              `dpx api <name>` 而非走 UI；advisory 现算
                              不入缓存，拒答路径同带（DPX_API_HINT=0 关）
          confidence_tier  high/medium/low——conf+margin 综合分档
                           （meta 规则短路标 'rule'，非置信语义；
                           tier=refuse 时恒压 low——置信不足即该升级）
          should_escalate  unsure 或 confidence_tier=low → true——
                           上层可据此自动 escalate（refuse 含其中）
          alternatives     ranked 第2-3名备选 [{ref,name,score}]；
                           probs 排序优先，无 probs 用 lexical 序；
                           AGENT_UNSURE 时同样输出（detail JSON 内）

        \b
        常驻推荐（reranker）:
          冷启模型加载 ~6s 只在首次——dpx daemon-start+DPX_DAEMON=1
          或 dpx serve 常驻后单次 decide ~90ms。常驻进程内同
          (页面 epoch, goal) 二次 decide 命中决策缓存免 CE 重打分
          （cached:true 回显；--no-cache 或 DPX_NO_CACHE=1 关）。
        """
        page = _get_page(session)
        sess = load_session(session) or {}
        eng = engine or sess.get('agent_engine') or 'external'
        state = _page_state(page, session, max_opts=top_k,
                            recall_pool=(eng == 'reranker'))
        # L0 adapter_hint 现算（L0.5 契约）：url 取当次 state 的当前
        # tab URL——epoch 守卫天然成立；hint 不进缓存，命中后响应组装
        # 处仍按当次注册表注入（同 margin/tier 现算层）
        _ahint = adapter_hint((state.get('state') or {}).get('url'),
                              goal)
        prev_sig = _state_sig(session)
        # meta 决策确定性短路——done/blocked/scroll 走规则非模型
        # （语义学习对状态决策零增益已实测；规则命中即出判定）
        meta = _meta_decide(state, goal)
        if meta:
            cur_sig = state['epoch']
            _record_hash(session, cur_sig)
            ok(attach_hints(
                {'action': meta['action'], 'ref': meta.get('ref'),
                 'confidence': 1.0,
                 'confidence_tier': 'rule',   # 规则命中非置信语义，如实标
                 # 结构化置信三键与模型路径对齐——消费者按键存在性
                 # 统一取，不 KeyError（规则命中确定可信→不升级无备选）
                 'should_escalate': False,
                 'alternatives': [],
                 'deterministic': True, 'advisory': eng == 'reranker',
                 'reason': meta['reason'], 'epoch': cur_sig,
                 'options_count': len(state['options']),
                 'truncated': state['truncated'], 'engine': 'meta-rules',
                 'done': meta['action'] == 'done', 'stuck': False},
                adapter_hint=_ahint))
            return
        # --cascade：Julia 粗筛 top-3 候选集回传上层终裁，
        # 不自动执行（top-3 召回实测 100%，top-1 仅 60%）
        if cascade:
            resp, request = _ask_questions(
                state, goal, eng, session, answer, answer_file,
                no_cache=no_cache)
            if '_error' in resp:
                error('决策引擎失败', code='DECIDE_FAILED',
                      detail=resp['_error'])
                return
            cached = bool(resp.pop('_cache_hit', False))
            probs_t = (resp.get('probs') or {}).get('target') or {}
            ranked = sorted(probs_t.items(), key=lambda x: -x[1])[:3]
            if not ranked and resp.get('target'):
                ranked = [(str(resp['target']),
                           resp.get('confidence') or 0)]
            ok(attach_hints(
                {'candidates': [
                    {'ref': int(k) if str(k).isdigit() else k,
                     'prob': round(float(v), 4),
                     **next((o for o in state['options']
                             if str(o['ref']) == str(k)), {})}
                    for k, v in ranked],
                 'epoch': state['epoch'],
                 'engine': eng, 'goal': goal, 'cached': cached,
                 'note': '级联模式——请上层引擎对候选集终裁后执行'},
                adapter_hint=_ahint))
            return
        resp, request = _ask_questions(state, goal, eng, session,
                                       answer, answer_file,
                                       no_cache=no_cache)
        if '_error' in resp:
            error('决策引擎失败', code='DECIDE_FAILED',
                  detail=resp['_error'])
            return
        cached = bool(resp.pop('_cache_hit', False))
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
            'cached': cached,
        }
        if eng == 'reranker':
            result['advisory'] = True   # 只建议不执行——契约写入响应
            if resp.get('backend'):
                result['backend'] = resp['backend']   # 'ort'/'st' 实载回显
        # TYPE 动作只回 ref——value 由上层补
        if action in ('type', 'type_text', 'fill'):
            result['needs_value'] = True
        # margin 门：top1-top2 概率差——绝对置信不可信
        # （实测 conf=1.00 也会错，margin 能拦 3/4 miss）。
        # 阈值校准（eval-120 + engine_ab-20，bge_v24_tuned）：
        # 可拦 miss 的 margin 聚类 ≤0.0028（m29/x12 高 margin miss
        # 任何档都拦不住）；hit 侧 margin<0.005 仍有真歧义样本——
        # 0.005 = 真拦全保 + 假拒最小的平衡点（旧值 0.15 假拒 78%）。
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
            result['tier'] = _act_tier(conf, tier_src)
            if _RERANKER_FAM == 'bge':
                # bge 引擎路径——conf/margin 分布重叠实测不可 act
                if result['tier'] == 'act':
                    result['tier'] = 'confirm'
        # bge softmax 分布集中 0.16-0.28（conf 无分离信号）——
        # bge 下 min_conf 门槛失效，unsure 只判 margin
        _conf_gate = min_conf if _RERANKER_FAM != 'bge' else 0.0
        _unsure = bool(not done and (conf and conf < _conf_gate
                                     or margin < min_margin))
        # 结构化置信度三字段——agent 免自行拼"多确定+备选"：
        # confidence_tier 三档分档复用 _conf_gate 校准（bge conf 分布
        # 无区分度时 floor=0，low 只由 unsure 触发）；should_escalate
        # 供上层自动 escalate；alternatives 为 ranked 第2-3名备选——
        # 三字段先于 AGENT_UNSURE error 写入 result，拒答 detail 内
        # 同样携带（拒答场景备选最有用）。
        result['confidence_tier'] = _conf_tier(conf, margin, _unsure,
                                               conf_floor=_conf_gate)
        # refuse=置信不足建议放弃——与升级语义同源：tier=refuse 时
        # confidence_tier 恒压 low，should_escalate 随之 true；
        # 不修会出现 refuse+medium+escalate=false 两套置信表达打架
        #（t2 S14 bge conf 0.05 过门实测——bge floor=0 不触发
        # conf<floor，margin 过门不触发 unsure，refuse 漏标 low）
        if result.get('tier') == 'refuse':
            result['confidence_tier'] = 'low'
        result['should_escalate'] = bool(
            _unsure or result['confidence_tier'] == 'low')
        result['alternatives'] = _alternatives(
            state, goal, probs.get('target') or {}, target)
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
        # adapter_hint 挂在 hard-case 落盘之后——响应侧 advisory
        # 不入训练数据；拒答（AGENT_UNSURE）detail 同样携带（拒答恰是
        # 接口化最值钱场景，见 UI_API_LINKAGE.md L0 否决注脚）
        attach_hints(result, adapter_hint=_ahint)
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
          dpx agent-setup --engine jev        # 用 DPX_JEV_URL/DPX_JEV_MODEL env
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

    @cli.command('decide-feedback')
    @click.argument('goal')
    @session_option
    @click.option('--chosen-ref', type=int, required=True,
                  help='上层升级后实际选用并执行成功的 ref')
    @click.option('--success', is_flag=True, default=False,
                  help='执行成功证据旗——fail-closed：缺省不落盘')
    @click.option('--post-state', default=None,
                  help='执行后状态证据 JSON（可选，如 '
                       '\'{"url":"https://…"}\'）')
    def decide_feedback(goal, session, chosen_ref, success, post_state):
        """escalate 反哺回写：decide 升级后上层自答成功的正例落盘。

        闭环场景：`dpx decide` 返回 should_escalate=true
        （confidence_tier=low / tier=refuse）→ 上层调 LLM/换策略
        自行答出 chosen_ref → 执行成功（页面有变化）→ 本命令把
        正例回写 ~/.dp_cli/hard_cases.jsonl（prov='escalate_feedback'
        +feedback='escalated'，schema_v=2 可选字段，review_hard_cases
        透传）——升级后答对的经验不再丢，攒成主动学习原料。

        fail-closed：--success 证据旗缺省不落盘——"答对"须带
        执行成功证据；--post-state 可附执行后快照佐证。
        serve 渠道等价请求：{"argv":["decide-feedback","<goal>",
        "--chosen-ref","N","--success"]}。

        \b
        示例:
          dpx decide-feedback "点归档" --chosen-ref 3 --success \\
              --post-state '{"url":"https://x/inbox"}'
        """
        ps = None
        if post_state:
            try:
                ps = json.loads(post_state)
            except json.JSONDecodeError as e:
                error(f'--post-state 非合法 JSON: {e}',
                      code='INVALID_ARGS')
                return
            if not isinstance(ps, dict):
                error('--post-state 须为 JSON 对象',
                      code='INVALID_ARGS')
                return
        if not success:
            error('缺 --success 证据旗——无执行成功证据不反哺落盘',
                  code='FEEDBACK_REJECTED')
            return
        from dp_cli.decide_train import log_escalate_feedback
        if log_escalate_feedback(session, goal, chosen_ref,
                                 escalate=True, success=True,
                                 post_state=ps):
            ok({'logged': True, 'goal': goal,
                'chosen_ref': chosen_ref,
                'prov': 'escalate_feedback',
                'feedback': 'escalated'},
               msg='escalate 反馈已落盘')
        else:
            error('反馈未落盘（DPX_HARD_CASES=0 已关或参数不足）',
                  code='FEEDBACK_REJECTED')
