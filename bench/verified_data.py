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
