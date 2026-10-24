# -*- coding: utf-8 -*-
"""decide 训练数据采集——交互命令即标注源（opt-in）。

开启：DPX_RECORD_DECIDE=1
落盘：~/.dp_cli/decide_train.jsonl
  {ts, session, state:{url, refs:[{ref,role,name}…]}, chosen:{cmd, argv}}
导出（decide-train-export）：reranker 训练格式
  {query, positive, negatives[]}——query=state 描述拼接，
  positive=chosen 命令文本，negatives=同 state 其他候选文本。
"""
import json
import os
import time
from pathlib import Path

from dp_cli.session import get_session_dir

_FLAG = 'DPX_RECORD_DECIDE'
_SKIP = {'ping', 'help', 'exit', 'quit', 'snapshot', 'agent-state',
         'decide-train-export'}


def enabled() -> bool:
    return os.environ.get(_FLAG) == '1'


def _file() -> Path:
    return get_session_dir() / 'decide_train.jsonl'


def record(session: str, argv: list, refs: dict,
           goal: str = None, chosen_ref: int = None) -> None:
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
    try:
        f = _file()
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open('a', encoding='utf-8') as fp:
            fp.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except OSError:
        pass          # 采集失败不阻塞主链路（录制非关键路径）


def _render(o: dict) -> str:
    return f"{o['role']} {o['name']}".strip()


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
        if d.get('goal'):           # decide 路径：goal=推理分布 query
            query = d['goal']
            pos_ref = d.get('chosen_ref')
            src = 'decide'          # 弱标注——引擎 pick 非用户确认
        else:
            m = re.search(r'@e(\d+)', chosen)
            if m:
                pos_ref = int(m.group(1))
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
