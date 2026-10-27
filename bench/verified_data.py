"""Admission check for outcome-backed element training rows.

Proofs are auditable receipts, not a substitute for reviewing whether the
assertion actually expresses the user's goal.
"""
from urllib.parse import urlsplit


def _render(option):
    return f"{option['role']} {option['name']}".strip()


def has_outcome_evidence(row):
    """Require a selected pre-action ref and a changed, asserted page value."""
    if not isinstance(row, dict):
        return False
    try:
        proof = row['evidence']
        refs = proof['before_refs']
        ref = proof['chosen_ref']
        action = proof['action']
        before = proof['before']
        after = proof['after']
        argv = action['argv']
        if (type(ref) is not int or ref <= 0 or not isinstance(refs, list)
                or not refs or not row['query'].strip()
                or proof['url'] != row['page']
                or before['expr'] != after['expr']
                or not before['expr'].strip()
                or type(action['exit']) is not int or action['exit'] != 0
                or action['result']['status'] != 'ok'
                or before['result']['status'] != 'ok'
                or after['result']['status'] != 'ok'):
            return False
        ids = [o['ref'] for o in refs]
        if len(ids) != len(set(ids)) or ref not in ids:
            return False
        chosen = next(o for o in refs if o['ref'] == ref)
        positive = _render(chosen)
        negatives = [_render(o) for o in refs if o['ref'] != ref]
        if (not positive or row['positive'] != positive
                or row['negatives'] != negatives
                or not isinstance(argv, list) or len(argv) < 2
                or argv[0] not in ('click', 'fill', 'check')
                or argv[1] not in (f'ref:{ref}', f'@e{ref}')):
            return False
        if argv[0] == 'fill':
            if len(argv) != 3 or after['expected'] != argv[2]:
                return False
        elif len(argv) != 2:
            return False
        old = before['result']['data']['result']
        new = after['result']['data']['result']
        expected = after['expected']
        return (type(old) is type(expected) and type(new) is type(expected)
                and old != expected and new == expected)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def eligible(row):
    """Only reviewed real-site evidence may enter normal model training."""
    if not isinstance(row, dict):
        return False
    page = row.get('page')
    return (row.get('src') == 'verified_real'
            and isinstance(page, str)
            and urlsplit(page).scheme in ('http', 'https')
            and has_outcome_evidence(row))


def promote_recorded(row):
    """录制行 → eligible 行：pre/post_state 快照 diff 推导断言。

    url diff→导航断言；无 diff/缺快照→None 不提升。
    provenance='record_derived' 标记快照推导 vs 采集器现场断言——
    下游可按 provenance 降权/分流，门禁语义不降（仍需过 eligible()）。
    """
    if not isinstance(row, dict) or row.get('unsure'):
        return None
    goal = (row.get('goal') or '').strip()
    refs = (row.get('state') or {}).get('refs') or []
    pre, post = row.get('pre_state'), row.get('post_state')
    argv = (row.get('chosen') or '').split()
    ref = row.get('engine_ref') or row.get('chosen_ref')
    if not (goal and refs and pre and post
            and type(ref) is int and len(argv) >= 2):
        return None
    url_pre, url_post = pre.get('url'), post.get('url')
    # 断言推导：url 变化→导航断言；同源 sig 变化→DOM 状态断言
    if url_pre and url_post and url_pre != url_post:
        expr = 'location.href'
        old, new = url_pre, url_post
    elif pre.get('sig') and post.get('sig') \
            and pre['sig'] != post['sig']:
        expr = 'document.body.innerText.length'
        old, new = pre['sig'], post['sig']
    else:
        return None                       # 无确定变化不提升
    if not isinstance(argv[1], str) or not argv[1].startswith('@e'):
        return None
    chosen = next((o for o in refs if o.get('ref') == ref), None)
    if not chosen:
        return None
    out = {
        'query': goal, 'page': url_pre or url_post, 'src': 'verified_real',
        'prov': 'record_derived',          # provenance：快照推导断言
        'positive': _render(chosen),
        'negatives': [_render(o) for o in refs if o.get('ref') != ref],
        'evidence': {
            'before_refs': refs, 'chosen_ref': ref, 'url': url_pre,
            'action': {'argv': [argv[0], f'ref:{ref}'], 'exit': 0,
                       'result': {'status': 'ok'}},
            'before': {'expr': expr,
                       'result': {'status': 'ok', 'data': {'result': old}}},
            'after': {'expr': expr, 'expected': new,
                      'result': {'status': 'ok', 'data': {'result': new}}},
        },
    }
    return out if eligible(out) else None
