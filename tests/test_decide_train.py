

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


def test_export_pairs_engine_ref_fallback(tmp_path, monkeypatch):
    """断点2：生产录制（serve/daemon 渠道）只写 engine_ref、无
    chosen_ref——export_pairs 兜底取 engine_ref 产出 src='decide'
    正例，不再 pos_ref=None 整行 skip；chosen_ref 人工覆盖仍优先。"""
    import dp_cli.decide_train as dt
    f = tmp_path / 'decide_train.jsonl'
    monkeypatch.setattr(dt, '_file', lambda: f)
    monkeypatch.setenv('DPX_RECORD_DECIDE', '1')
    refs = {'1': {'role': 'link', 'name': '首页'},
            '2': {'role': 'button', 'name': '登录'}}
    # 引擎建议行（生产形态：goal+engine_ref，无 chosen_ref）
    dt.record('s', ['decide', '点登录'], refs, goal='点登录', engine_ref=2)
    pairs = dt.export_pairs(f)
    assert len(pairs) == 1
    assert pairs[0]['positive'] == 'button 登录'
    assert pairs[0]['query'] == '点登录' and pairs[0]['src'] == 'decide'
    # chosen_ref（人工覆盖）优先于 engine_ref
    dt.record('s', ['decide', '点首页'], refs, goal='点首页',
              chosen_ref=1, engine_ref=2)
    pairs = dt.export_pairs(f)
    assert len(pairs) == 2 and pairs[1]['positive'] == 'link 首页'


def test_promote_recorded_ref_colon_form():
    """断点4：chosen 'click ref:N' 形态与 '@eN' 等价可提升；
    argv ref 与 engine_ref 错配/非 ref 形态不提升（防正例错配）。"""
    import sys
    sys.path.insert(0, 'bench')
    from verified_data import promote_recorded, eligible
    refs = [{'ref': 1, 'role': 'link', 'name': 'Sign in'},
            {'ref': 2, 'role': 'button', 'name': 'OK'}]
    base = {'goal': '登录网站', 'chosen': 'click ref:1', 'engine_ref': 1,
            'state': {'refs': refs},
            'pre_state': {'url': 'https://x/login'},
            'post_state': {'url': 'https://x/home'}}
    r = promote_recorded(dict(base))
    assert r and eligible(r) and r['prov'] == 'record_derived'
    assert r['positive'] == 'link Sign in'
    # @eN 形态同样可提升（回归）
    ok2 = dict(base); ok2['chosen'] = 'click @e1'
    assert promote_recorded(ok2)
    # argv ref 与 engine_ref 错配→不提升（原判据只看 @e 前缀会漏）
    bad = dict(base); bad['chosen'] = 'click ref:2'
    assert promote_recorded(bad) is None
    bad2 = dict(base); bad2['chosen'] = 'click @e2'
    assert promote_recorded(bad2) is None
    # 非 ref 定位形态→不提升
    bad3 = dict(base); bad3['chosen'] = 'click text:登录'
    assert promote_recorded(bad3) is None


def test_export_pairs_same_source_skipped(tmp_path, monkeypatch):
    """P2-契约：chosen_ref==engine_ref 同源污染行 export 整行
    skip——"人工覆盖"实为引擎建议自身，不产训练对防自我强化；
    仅 engine_ref 行与 chosen≠engine 行不受影响。"""
    import dp_cli.decide_train as dt
    f = tmp_path / 'decide_train.jsonl'
    monkeypatch.setattr(dt, '_file', lambda: f)
    monkeypatch.setenv('DPX_RECORD_DECIDE', '1')
    refs = {'1': {'role': 'link', 'name': '首页'},
            '2': {'role': 'button', 'name': '登录'}}
    # 同源污染行：chosen_ref == engine_ref → skip
    dt.record('s', ['decide', '点登录'], refs, goal='点登录',
              chosen_ref=2, engine_ref=2)
    assert dt.export_pairs(f) == []
    # chosen≠engine（真人工覆盖）→ 照常产出
    dt.record('s', ['decide', '点首页'], refs, goal='点首页',
              chosen_ref=1, engine_ref=2)
    # 仅 engine_ref（无人工覆盖）→ 弱标注照常产出
    dt.record('s', ['decide', '再点登录'], refs, goal='再点登录',
              engine_ref=2)
    pairs = dt.export_pairs(f)
    assert len(pairs) == 2
    assert pairs[0]['positive'] == 'link 首页'
    assert pairs[1]['positive'] == 'button 登录'


def test_decide_train_jsonl_trim(tmp_path, monkeypatch):
    """decide_train.jsonl 超水位截留尾部近似 ring——复用 netlog
    trim 模式；未超水位不整读不裁剪。"""
    import dp_cli.decide_train as dt
    monkeypatch.setenv('DPX_RECORD_DECIDE', '1')
    f = tmp_path / 'r.jsonl'
    monkeypatch.setattr(dt, '_file', lambda: f)
    refs = {'1': {'role': 'link', 'name': 'Sign in'}}
    # 直接造超水位文件再录一条触发 trim
    f.write_text(''.join(
        '{"ts":%d,"chosen":"x"}\n' % i
        for i in range(dt._TRIM_HIGH + 10)))
    dt.record('s', ['click', '@e1'], refs)
    lines = f.read_text().splitlines()
    assert len(lines) <= dt._MAX_LINES + 1
    assert not (tmp_path / 'r.jsonl.tmp').exists()


def test_decide_train_trim_below_high_water_untouched(tmp_path,
                                                    monkeypatch):
    """未超水位：record 不触发裁剪，行数原样增长。"""
    import dp_cli.decide_train as dt
    monkeypatch.setenv('DPX_RECORD_DECIDE', '1')
    f = tmp_path / 'r.jsonl'
    monkeypatch.setattr(dt, '_file', lambda: f)
    refs = {'1': {'role': 'link', 'name': 'Sign in'}}
    dt.record('s', ['click', '@e1'], refs)
    dt.record('s', ['click', '@e1'], refs)
    assert len(f.read_text().splitlines()) == 2
