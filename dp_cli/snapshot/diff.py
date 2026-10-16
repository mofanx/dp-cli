# -*- coding:utf-8 -*-
"""快照增量比对：指纹提取 / 基线存取 / diff 计算。

指纹以 backendNodeId 为主键（同一文档生命周期内 CDP 侧稳定），
无 backendNodeId 的补充探测元素回退 role|name|locator 哈希。
"""
import hashlib
import json
from datetime import datetime

from dp_cli.session import get_session_dir

_KEEP = 5  # 每会话保留的基线数


def fingerprint(snapshot: dict) -> dict:
    """a11y 快照 → {key: sig} 扁平指纹表。
    key: backendNodeId 或 'h:'+短哈希；sig: role|name|locator。"""
    items = {}

    def walk(node):
        sig = '|'.join(str(node.get(k) or '')
                       for k in ('role', 'name', 'locator'))
        bid = node.get('backendNodeId')
        if bid is not None:
            key = str(bid)
        else:
            key = 'h:' + hashlib.md5(sig.encode()).hexdigest()[:12]
        items[key] = sig
        for c in node.get('children') or []:
            walk(c)

    walk(snapshot.get('tree') or {})
    return items


def save_baseline(session: str, url: str, items: dict) -> str:
    """存储当前快照为基线，返回 ts id（ISO）。"""
    ts = datetime.now().isoformat(timespec='milliseconds')
    f = _file(session)
    hist = _load(f)
    hist[ts] = {'url': url, 'items': items}
    for k in sorted(hist)[:-_KEEP]:        # 只留最近 _KEEP 个
        hist.pop(k)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(hist, ensure_ascii=False), encoding='utf-8')
    return ts


def get_baseline(session: str, since: str):
    """since='last' → 最新基线；否则按 ts 精确匹配。返回 {ts,url,items}|None。"""
    hist = _load(_file(session))
    if not hist:
        return None
    if since == 'last':
        ts = sorted(hist)[-1]
    else:
        ts = since if since in hist else None
        if ts is None:
            return None
    return {'ts': ts, **hist[ts]}


def diff_items(old: dict, new: dict) -> dict:
    """{key: sig} 两表比对 → added/removed/changed/churn（元素级）。

    churn：innerHTML 重建等导致 backendNodeId 换血——同一逻辑元素
    （sig 一致）在 removed+added 中成对出现，归类为换血而非真实增删。
    """
    added = [{'key': k, 'sig': v} for k, v in new.items() if k not in old]
    removed = [{'key': k, 'sig': v} for k, v in old.items() if k not in new]
    changed = [{'key': k, 'old': old[k], 'new': new[k]}
               for k in new if k in old and new[k] != old[k]]

    add_by_sig = {}
    for it in added:
        add_by_sig.setdefault(it['sig'], []).append(it)
    churn, keep_removed = [], []
    for it in removed:
        pool = add_by_sig.get(it['sig'])
        if pool:
            churn.append({'old_key': it['key'],
                          'new_key': pool.pop()['key'], 'sig': it['sig']})
        else:
            keep_removed.append(it)
    added = [it for pool in add_by_sig.values() for it in pool]
    return {'added': added, 'removed': keep_removed,
            'changed': changed, 'churn': churn}


def render_diff_text(diff: dict, url: str, base_ts: str) -> str:
    """文本渲染：+ 新增 / - 移除 / ~ 变更。"""
    def fmt(sig):
        role, _, rest = (sig or '').partition('|')
        name, _, loc = rest.partition('|')
        return f'{role} "{name}"' + (f' → {loc}' if loc else '')

    lines = [f'### Snapshot Diff (vs {base_ts})', f'- URL: {url}', '']
    for it in diff['added']:
        lines.append(f'+ {fmt(it["sig"])}')
    for it in diff['removed']:
        lines.append(f'- {fmt(it["sig"])}')
    for it in diff['changed']:
        lines.append(f'~ {fmt(it["old"])} ⇒ {fmt(it["new"])}')
    for it in diff.get('churn', []):
        lines.append(f'↻ {fmt(it["sig"])}（重建换血）')
    if not any(diff.values()):
        lines.append('（无变化）')
    return '\n'.join(lines)


def _file(session: str):
    return get_session_dir() / 'snaps' / f'{session or "default"}.json'


def _load(f) -> dict:
    try:
        return json.loads(f.read_text(encoding='utf-8'))
    except Exception:
        return {}
