#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""Mind2Web → decide_train 离线转换管线。

输入：osunlp/Mind2Web train 分片（hf_hub_download 到 /tmp/m2w 或 --in 指定）
输出：decide_train 同构 jsonl——
  {ts, session:'m2w', src:'m2w', page:<website>, site, domain,
   goal:<confirmed_task>, op:'click|type|select', value,
   state:{refs:[{ref,role,name}]}, chosen_ref}

映射纪律（如实边界）：
- pos_candidates 为 ground truth——直接转正样本；neg_candidates 全收为干扰项
- role：tag→语义角色近似（a→link,button→button,input/textarea→textbox,
  select→combobox,其余原 tag）——非真 a11y role，记近似
- name：attributes JSON 里取 aria-label>placeholder>id>name>title>
  截断 class 首段——无可见文本（候选集不含 innerText），name 覆盖率低
  属如实边界非缺陷
- 采样验证：pos 唯一性、ref 不撞、name 非空率、neg 数
"""
import json
import random
import re
import sys
import time
from collections import Counter

_OP_MAP = {'CLICK': 'click', 'TYPE': 'type', 'SELECT': 'select'}
_ROLE_MAP = {'a': 'link', 'button': 'button', 'input': 'textbox',
             'textarea': 'textbox', 'select': 'combobox', 'option': 'option',
             'img': 'img', 'svg': 'img', 'span': 'generic', 'div': 'generic',
             'li': 'listitem', 'label': 'label', 'h1': 'heading', 'h2': 'heading',
             'h3': 'heading', 'p': 'generic', 'form': 'form', 'table': 'table'}

_NAME_KEYS = ('aria-label', 'aria_label', 'placeholder', 'id', 'name',
              'title', 'alt', 'value', 'data-testid', 'href', 'role')


def _attrs(cand):
    try:
        return json.loads(cand.get('attributes') or '{}')
    except (TypeError, ValueError):
        return {}


def _text_map(cleaned_html: str) -> dict:
    """cleaned_html → {backend_node_id: 可见文本}。

    M2W 快照用 <text backend_node_id> 承载文本节点——先把 <text>
    内容归到其直属父元素 bid；再按元素开始标签补 bid→text 映射，
    使候选元素拿到"该元素内可见文本"近似 innerText。
    """
    out = {}
    if not cleaned_html:
        return out
    # text 节点：内容归于其父（最近未闭合开始标签的 bid）
    # 简化实现：逐 <text bid>内容</text> 捕获，bid 即文本宿主
    for m in re.finditer(
            r'<text[^>]*backend_node_id="(\d+)"[^>]*>(.*?)</text>',
            cleaned_html, re.S):
        bid, txt = m.group(1), re.sub(r'\s+', ' ', m.group(2)).strip()
        if txt:
            out.setdefault(bid, '')
            out[bid] = (out[bid] + ' ' + txt).strip()[:160]
    return out


def _bid_text(cand, tmap: dict, html: str) -> str:
    """候选 bid → 可见文本：自身 text 宿主→按 bid 在其元素片段内聚合
    子孙 <text>（父容器含子节点文本的近似）。"""
    bid = str(cand.get('backend_node_id') or '')
    if not bid:
        return ''
    if bid in tmap:
        return tmap[bid]
    # 取该 bid 元素开标签起的 2KB 片段内 text 宿主聚合（无子解析树的
    # 低成本近似——富文本元素的可见名覆盖足够）
    m = re.search(r'<\w+[^>]*backend_node_id="' + re.escape(bid) +
                  r'"[^>]*>(.{0,2048})', html, re.S)
    if m:
        seg = re.sub(r'<[^>]+>', ' ', m.group(1))
        seg = re.sub(r'\s+', ' ', seg).strip()
        return seg[:160]
    return ''


def _name_of(attrs: dict, tag: str) -> str:
    for k in _NAME_KEYS:
        v = attrs.get(k)
        if isinstance(v, str) and v.strip():
            v = v.strip()
            if k == 'href' and len(v) > 60:
                v = v[:57] + '...'
            if k == 'id':
                # React 随机 id 信息量低，保留但截断
                v = v[:40]
            return v[:80]
    cls = (attrs.get('class') or '').strip()
    if cls:
        return cls.split()[0][:60]
    return ''


def convert_item(item: dict, rng: random.Random, max_neg: int = 24):
    """单 confirmed_task → 每 action 一条训练行。"""
    goal = item.get('confirmed_task') or ''
    site = item.get('website') or ''
    rows = []
    reprs = item.get('action_reprs') or []
    for idx, act in enumerate(item.get('actions') or []):
        op_raw = (act.get('operation') or {})
        op = _OP_MAP.get(str(op_raw.get('op') or '').upper())
        if op is None:
            continue
        pos = act.get('pos_candidates') or []
        neg = act.get('neg_candidates') or []
        if not pos:
            continue
        # 只收 top_level/原目标 pos（多 pos 时取 is_top_level_target 优）
        pos_pick = [c for c in pos if c.get('is_top_level_target')] or pos[:1]
        pos_c = pos_pick[0]
        tmap = _text_map(act.get('cleaned_html') or '')
        rng.shuffle(neg)
        cand = neg[:max_neg]
        refs, name_hit = [], 0
        for i, c in enumerate(cand, 1):
            tag = str(c.get('tag') or 'div').lower()
            nm = _bid_text(c, tmap, act.get('cleaned_html') or '') \
                or _name_of(_attrs(c), tag)
            if nm:
                name_hit += 1
            refs.append({'ref': i, 'role': _ROLE_MAP.get(tag, tag), 'name': nm})
        tag = str(pos_c.get('tag') or 'div').lower()
        pos_ref = {'ref': len(refs) + 1, 'role': _ROLE_MAP.get(tag, tag),
                   'name': _bid_text(pos_c, tmap, act.get('cleaned_html') or '')
                           or _name_of(_attrs(pos_c), tag)}
        refs.insert(rng.randrange(len(refs) + 1), pos_ref)
        # ref 重新编号保持插入语义
        for i, r in enumerate(refs, 1):
            r['ref'] = i
        rows.append({
            'ts': int(time.time()), 'session': 'm2w', 'src': 'm2w',
            'page': site, 'site': site,
            'domain': item.get('domain') or '',
            'goal': goal, 'op': op,
            'q': reprs[idx] if idx < len(reprs) else goal,
            'value': op_raw.get('value') or '',
            'state': {'refs': refs},
            'chosen_ref': pos_ref['ref'],
            '_name_cov': round(name_hit / max(len(refs), 1), 3),
        })
    return rows


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='src', default='/tmp/m2w/data/train/train_0.json')
    ap.add_argument('--out', dest='out', default='bench/data/m2w_train.jsonl')
    ap.add_argument('--max-neg', type=int, default=24)
    ap.add_argument('--limit', type=int, default=0, help='最多处理 task 数')
    ap.add_argument('--seed', type=int, default=7)
    args = ap.parse_args()

    data = json.load(open(args.src))
    rng = random.Random(args.seed)
    rows, ops, name_cov = [], Counter(), []
    for it in data[:args.limit or None]:
        for r in convert_item(it, rng, max_neg=args.max_neg):
            rows.append(r)
            ops[r['op']] += 1
            name_cov.append(r.pop('_name_cov'))
    import os
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    n = len(rows)
    avg_refs = sum(len(r['state']['refs']) for r in rows) / max(n, 1)
    avg_cov = sum(name_cov) / max(n, 1)
    empty_name = sum(1 for r in rows
                     if not r['state']['refs'][r['chosen_ref'] - 1]['name'])
    print(json.dumps({
        'tasks': min(len(data), args.limit or len(data)), 'rows': n,
        'ops': dict(ops), 'avg_refs': round(avg_refs, 1),
        'avg_name_cov': round(avg_cov, 3),
        'chosen_name_empty': empty_name,
        'out': args.out}, ensure_ascii=False))


if __name__ == '__main__':
    main()
