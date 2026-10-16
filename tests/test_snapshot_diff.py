# -*- coding:utf-8 -*-
"""snapshot --since 增量 diff：指纹/基线/比对单元测试（纯 dict，无浏览器）。"""
import pytest

from dp_cli.snapshot.diff import (fingerprint, save_baseline, get_baseline,
                                  diff_items, render_diff_text)


def _snap(nodes):
    return {'tree': {'role': 'WebArea', 'name': 'p', 'children': nodes}}


def _node(bid=None, role='button', name='x', loc='', children=None):
    n = {'role': role, 'name': name, 'locator': loc}
    if bid is not None:
        n['backendNodeId'] = bid
    if children:
        n['children'] = children
    return n


@pytest.mark.unit
def test_fingerprint_flattens_tree_with_bid_key():
    fp = fingerprint(_snap([_node(1, 'button', 'ok'),
                            _node(2, 'link', 'a', children=[_node(3)])]))
    assert fp['1'] == 'button|ok|'
    assert fp['3'] == 'button|x|'    # 孙节点递归
    assert set(fp) - {'1', '2', '3'} == {k for k in fp if k.startswith('h:')}


@pytest.mark.unit
def test_fingerprint_fallback_hash_key():
    fp = fingerprint(_snap([_node(None, 'div', 'ico')]))
    assert list(fp)[0].startswith('h:')


@pytest.mark.unit
def test_diff_added_removed_changed():
    old = {'1': 'button|ok|', '2': 'link|a|', '3': 'div|x|'}
    new = {'1': 'button|ok|', '2': 'link|b|', '4': 'div|new|'}
    d = diff_items(old, new)
    assert [i['key'] for i in d['added']] == ['4']
    assert [i['key'] for i in d['removed']] == ['3']
    assert d['changed'][0]['old'] == 'link|a|' and d['changed'][0]['new'] == 'link|b|'


@pytest.mark.unit
def test_baseline_roundtrip_and_last(tmp_path, monkeypatch):
    import dp_cli.snapshot.diff as diff_mod
    monkeypatch.setattr(diff_mod, '_file',
                        lambda s: tmp_path / f'{s or "default"}.json')
    import json, time
    ts1 = save_baseline('s1', 'http://a', {'1': 'a'})
    time.sleep(0.01)
    ts2 = save_baseline('s1', 'http://a', {'1': 'b'})
    assert ts1 != ts2                     # 毫秒精度避免同秒覆盖
    assert get_baseline('s1', 'last')['items'] == {'1': 'b'}
    assert get_baseline('s1', ts1)['items'] == {'1': 'a'}
    assert get_baseline('s1', '9999') is None
    assert get_baseline('noexist', 'last') is None


@pytest.mark.unit
def test_render_diff_text_marks():
    d = {'added': [{'key': '1', 'sig': 'button|new|css:.b'}],
         'removed': [{'key': '2', 'sig': 'link|gone|'}],
         'changed': [{'key': '3', 'old': 'div|old|', 'new': 'div|new|'}]}
    out = render_diff_text(d, 'u', 't0')
    assert '+ button "new"' in out and '- link "gone"' in out
    assert '~ div "old" ⇒ div "new"' in out


@pytest.mark.unit
def test_diff_churn_pairs_bid_rekey():
    """innerHTML 重建换血：同一 sig 在 removed+added 成对 → churn，
    不计入真实增删。"""
    old = {'10': 'link|a2|css:a', '20': 'StaticText|a2|', '30': 'b|x|'}
    new = {'41': 'link|a2|css:a', '42': 'StaticText|a2|', '30': 'b|x|'}
    d = diff_items(old, new)
    assert d['added'] == [] and d['removed'] == []
    assert sorted((c['old_key'], c['new_key']) for c in d['churn']) == [
        ('10', '41'), ('20', '42')]


@pytest.mark.unit
def test_diff_churn_mixed_with_real_changes():
    """换血与真实增删并存：配对部分进 churn，未配对部分仍计增删。"""
    old = {'1': 'link|keep|', '2': 'button|gone|', '3': 'div|stay|'}
    new = {'9': 'link|keep|', '7': 'link|fresh|', '3': 'div|stay|'}
    d = diff_items(old, new)
    assert [i['key'] for i in d['added']] == ['7']
    assert [i['key'] for i in d['removed']] == ['2']
    assert len(d['churn']) == 1 and d['churn'][0]['old_key'] == '1'


@pytest.mark.unit
def test_render_diff_text_churn_marker():
    d = {'added': [], 'removed': [], 'changed': [],
         'churn': [{'old_key': '1', 'new_key': '9', 'sig': 'link|a|css:x'}]}
    out = render_diff_text(d, 'u', 't0')
    assert '↻ link "a"' in out and '重建换血' in out
    assert '（无变化）' not in out   # 仅 churn 也要如实显示
