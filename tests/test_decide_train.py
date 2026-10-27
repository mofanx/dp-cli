

def test_record_post_state_field(tmp_path, monkeypatch):
    """post_state 快照落盘——真实流量执行后证据字段；unsure 语义不变。"""
    import dp_cli.decide_train as dt
    monkeypatch.setenv('DPX_RECORD_DECIDE', '1')
    monkeypatch.setattr(dt, '_file', lambda: tmp_path / 'r.jsonl')
    refs = {'1': {'role': 'link', 'name': 'Sign in'}}
    dt.record('s', ['click', '@e1'], refs,
              post_state={'url': 'https://x/logged'})
    dt.record('s', ['decide', '登录'], refs, unsure=True,
              post_state={'url': 'https://x/'})
    import json
    rows = [json.loads(l) for l in (tmp_path / 'r.jsonl').read_text().splitlines()]
    assert rows[0]['post_state']['url'] == 'https://x/logged'
    # unsure 记录带 post_state 但 unsure 标记保留——不冒充标签
    assert rows[1]['unsure'] is True and rows[1]['post_state']['url'] == 'https://x/'


def test_promote_recorded_url_diff():
    """录制行 pre/post url diff→promote 为 eligible；无 diff/缺字段不提升。"""
    import sys
    sys.path.insert(0, 'bench')
    from verified_data import promote_recorded, eligible
    refs = [{'ref': 1, 'role': 'link', 'name': 'Sign in'},
            {'ref': 2, 'role': 'button', 'name': 'OK'}]
    base = {'goal': '登录网站', 'chosen': 'click @e1', 'engine_ref': 1,
            'state': {'refs': refs},
            'pre_state': {'url': 'https://x/login'},
            'post_state': {'url': 'https://x/home'}}
    r = promote_recorded(dict(base))
    assert r and eligible(r) and r['prov'] == 'record_derived'
    assert r['positive'] == 'link Sign in'
    # 无 diff→不提升
    bad = dict(base); bad['post_state'] = {'url': 'https://x/login'}
    assert promote_recorded(bad) is None
    # unsure 行→不提升
    bad2 = dict(base); bad2['unsure'] = True
    assert promote_recorded(bad2) is None
    # 缺 pre_state→不提升
    bad3 = dict(base); bad3.pop('pre_state')
    assert promote_recorded(bad3) is None


def test_promote_derived_default_rejected(tmp_path, monkeypatch):
    """prov=record_derived 默认拒收（--allow-derived 才放行）+sig 路径已删。"""
    import sys, json
    sys.path.insert(0, 'bench')
    from merge_train import merge
    from verified_data import promote_recorded
    row = {'query': '登录网站', 'page': 'https://x/login', 'src': 'verified_real',
           'prov': 'record_derived', 'positive': 'link Sign in',
           'negatives': [], 'evidence': {
               'before_refs': [{'ref': 1, 'role': 'link', 'name': 'Sign in'}],
               'chosen_ref': 1, 'url': 'https://x/login',
               'action': {'argv': ['click', 'ref:1'], 'exit': 0,
                          'result': {'status': 'ok'}},
               'before': {'expr': 'location.href',
                          'result': {'status': 'ok',
                                     'data': {'result': 'https://x/login'}}},
               'after': {'expr': 'location.href',
                         'expected': 'https://x/home',
                         'result': {'status': 'ok',
                                    'data': {'result': 'https://x/home'}}}}}
    f = tmp_path / 'in.json'; f.write_text(json.dumps([row]))
    merge([str(f)], str(tmp_path / 'out.json'))
    assert json.loads((tmp_path / 'out.json').read_text()) == []
    merge([str(f)], str(tmp_path / 'out2.json'), allow_derived=True)
    assert len(json.loads((tmp_path / 'out2.json').read_text())) == 1
    # sig diff 行不提升（硬证据只剩 url）
    r = promote_recorded({'goal': 'g', 'chosen': 'click @e1', 'engine_ref': 1,
                          'state': {'refs': [{'ref': 1, 'role': 'b',
                                              'name': 'x'}]},
                          'pre_state': {'sig': 'a', 'url': 'https://x/'},
                          'post_state': {'sig': 'b', 'url': 'https://x/'}})
    assert r is None
