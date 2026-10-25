"""Outcome evidence is required before a new row can enter training."""
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bench'))
from merge_train import merge
from train_reranker import load_pairs
from verified_data import eligible, has_outcome_evidence


def _row():
    page = 'https://example.test/demo'
    return {
        'src': 'verified_real',
        'page': page,
        'query': '让演示页的按钮触发一次计数',
        'positive': 'button Run',
        'negatives': ['button Cancel'],
        'evidence': {
            'url': page,
            'before_refs': [
                {'ref': 1, 'role': 'button', 'name': 'Run'},
                {'ref': 2, 'role': 'button', 'name': 'Cancel'},
            ],
            'chosen_ref': 1,
            'before': {'expr': 'window.counter',
                       'result': {'status': 'ok', 'data': {'result': 0}}},
            'action': {'argv': ['click', 'ref:1'], 'exit': 0,
                       'result': {'status': 'ok'}},
            'after': {'expr': 'window.counter', 'expected': 1,
                      'result': {'status': 'ok', 'data': {'result': 1}}},
        },
    }


def test_outcome_gate_rejects_unverified_and_false_receipts():
    valid = _row()
    assert has_outcome_evidence(valid) and eligible(valid)
    for mutate in (
        lambda r: r.pop('evidence'),
        lambda r: r['evidence']['action'].update(exit=1),
        lambda r: r['evidence']['action']['result'].update(status='error'),
        lambda r: r['evidence']['after']['result']['data'].update(result=0),
        lambda r: r['evidence']['before']['result']['data'].update(result=1),
        lambda r: r['evidence']['after'].update(expr='other'),
        lambda r: r['evidence']['action'].update(argv=['click', 'ref:2']),
        lambda r: r.update(positive='button Cancel'),
    ):
        bad = deepcopy(valid)
        mutate(bad)
        assert not eligible(bad)
    for src in ('decide', 'unsure', 'meta', 'synthetic', 'pilot_local', None):
        bad = deepcopy(valid)
        bad['src'] = src
        assert not eligible(bad)


def test_local_pilot_has_proof_but_cannot_train():
    row = _row()
    row['src'] = 'pilot_local'
    row['page'] = row['evidence']['url'] = 'file:///tmp/local.html'
    assert has_outcome_evidence(row)
    assert not eligible(row)
    row['src'] = 'verified_real'
    assert not eligible(row)


def test_malformed_row_fails_closed(tmp_path):
    assert not eligible(None) and not has_outcome_evidence(None)
    src, out = tmp_path / 'rows.json', tmp_path / 'out.json'
    src.write_text(json.dumps([None, _row()]))
    merge([str(src)], str(out))
    assert json.loads(out.read_text()) == [_row()]
    with pytest.raises(ValueError, match='行号 \\[1\\]'):
        load_pairs(src)


def test_train_requires_proof_by_default(tmp_path):
    valid = _row()
    src = tmp_path / 'rows.json'
    src.write_text(json.dumps([valid]))
    assert load_pairs(src) == [(valid['query'], 'button Run', 1.0),
                               (valid['query'], 'button Cancel', 0.0)]
    for label in ('decide', 'pilot_local'):
        old = deepcopy(valid)
        old['src'] = label
        old.pop('evidence')
        src.write_text(json.dumps([old]))
        with pytest.raises(ValueError, match='--allow-unverified'):
            load_pairs(src)
        assert load_pairs(src, allow_unverified=True) == [
            (valid['query'], 'button Run', 1.0),
            (valid['query'], 'button Cancel', 0.0),
        ]
    old['src'] = 'unsure'
    src.write_text(json.dumps([old]))
    assert load_pairs(src, allow_unverified=True) == []


def test_merge_refuses_legacy_and_pilot_by_default(tmp_path):
    valid = _row()
    old = deepcopy(valid)
    old['src'] = 'decide'
    old.pop('evidence')
    pilot = deepcopy(valid)
    pilot['src'] = 'pilot_local'
    source = tmp_path / 'rows.json'
    out = tmp_path / 'merged.json'
    source.write_text(json.dumps([old, pilot, valid]))
    merge([str(source)], str(out))
    assert json.loads(out.read_text()) == [valid]
    merge([str(source)], str(out), allow_bad_src=True)
    assert len(json.loads(out.read_text())) == 3


def test_ref_alias_is_valid_evidence():
    row = _row()
    row['evidence']['action']['argv'][1] = '@e1'
    assert eligible(row)


def test_override_still_drops_eval_overlap(tmp_path):
    from eval_cases import EVAL_CASES
    row = _row()
    row['query'] = EVAL_CASES[0]['goal']
    source, out = tmp_path / 'source.json', tmp_path / 'out.json'
    source.write_text(json.dumps([row]))
    merge([str(source)], str(out), allow_bad_src=True)
    assert json.loads(out.read_text()) == []
