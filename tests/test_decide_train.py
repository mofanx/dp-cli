

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
    rows = [json.loads(l) for l in (tmp_path / 'r.jsonl')
            .read_text(encoding='utf-8').splitlines()]
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
    f = tmp_path / 'in.json'
    f.write_text(json.dumps([row]), encoding='utf-8')
    merge([str(f)], str(tmp_path / 'out.json'))
    assert json.loads(
        (tmp_path / 'out.json').read_text(encoding='utf-8')) == []
    merge([str(f)], str(tmp_path / 'out2.json'), allow_derived=True)
    assert len(json.loads(
        (tmp_path / 'out2.json').read_text(encoding='utf-8'))) == 1
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
        for i in range(dt._TRIM_HIGH + 10)), encoding='utf-8')
    dt.record('s', ['click', '@e1'], refs)
    lines = f.read_text(encoding='utf-8').splitlines()
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
    assert len(f.read_text(encoding='utf-8').splitlines()) == 2


# ── hard-case 被动落盘（默认开，~/.dp_cli/hard_cases.jsonl）──────────
# conftest autouse 已把 _hard_file/_hard_lock_path 重定向到
# tmp_path/.dp_cli/——以下用例直接读该路径即可。

_HARD_STATE = {'state': {'url': 'https://x/login', 'title': 'L'},
               'options': [{'ref': 2, 'role': 'button', 'name': '登录'},
                           {'ref': 5, 'role': 'link', 'name': '注册'},
                           {'ref': 7, 'role': 'link', 'name': '帮助'}],
               'epoch': 'e1', 'truncated': False}


def _hard_result(conf, margin, probs=None, **kw):
    r = {'action': 'click', 'ref': 2, 'confidence': conf,
         'probs': probs if probs is not None else
                {'target': {'2': 0.4, '5': 0.33, '7': 0.02}},
         'margin': margin, 'margin_source': 'probs',
         'engine': 'reranker', 'done': False}
    r.update(kw)
    return r


def _hard_rows(tmp_path):
    import json as _j
    f = tmp_path / '.dp_cli' / 'hard_cases.jsonl'
    if not f.exists():
        return []
    return [_j.loads(l)
            for l in f.read_text(encoding='utf-8').splitlines()]


def test_hard_case_low_conf_logged(tmp_path):
    """conf<0.5 触发落盘；行 schema 齐（goal/top3 candidates ref+name+
    score/engine_ref/conf/margin/ts/session/url）。"""
    import dp_cli.decide_train as dt
    dt._maybe_log_hard_case(_hard_result(0.42, 0.3), 's1',
                            goal='点登录', state=_HARD_STATE, unsure=True)
    rows = _hard_rows(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    for k in ('ts', 'session', 'goal', 'url', 'engine', 'engine_ref',
              'conf', 'margin', 'margin_source', 'unsure', 'triggers',
              'candidates'):
        assert k in r, k
    assert r['session'] == 's1' and r['goal'] == '点登录'
    assert r['url'] == 'https://x/login'
    assert r['engine_ref'] == 2 and r['conf'] == 0.42
    assert r['unsure'] is True and r['triggers'] == ['low_conf']
    assert [c['ref'] for c in r['candidates']] == [2, 5, 7]
    c0 = r['candidates'][0]
    assert c0['name'] == '登录' and c0['role'] == 'button'
    assert abs(c0['score'] - 0.4) < 1e-6


def test_hard_case_confident_not_logged(tmp_path):
    """conf≥0.5 且 margin≥0.1 → 不落盘（文件不创建）。"""
    import dp_cli.decide_train as dt
    dt._maybe_log_hard_case(_hard_result(0.92, 0.6), 's1',
                            goal='点登录', state=_HARD_STATE)
    assert _hard_rows(tmp_path) == []


def test_hard_case_low_margin_logged(tmp_path):
    """conf≥0.5 但 top2 margin<0.1（歧义）同样落盘；
    conf/margin 双低时 triggers 两标。"""
    import dp_cli.decide_train as dt
    dt._maybe_log_hard_case(_hard_result(0.9, 0.05), 's1',
                            goal='x', state=_HARD_STATE)
    dt._maybe_log_hard_case(_hard_result(0.3, 0.05), 's1',
                            goal='y', state=_HARD_STATE)
    rows = _hard_rows(tmp_path)
    assert len(rows) == 2
    assert rows[0]['triggers'] == ['low_margin']
    assert rows[1]['triggers'] == ['low_conf', 'low_margin']


def test_hard_case_opt_out(tmp_path, monkeypatch):
    """DPX_HARD_CASES=0 显式关闭——低置信也不落盘。"""
    import dp_cli.decide_train as dt
    monkeypatch.setenv('DPX_HARD_CASES', '0')
    dt._maybe_log_hard_case(_hard_result(0.1, 0.01), 's1',
                            state=_HARD_STATE)
    assert _hard_rows(tmp_path) == []


def test_hard_case_state_fallback_load_refs(tmp_path, monkeypatch):
    """无内嵌 state 时走 load_refs/load_session 补 name/url——
    与录制路径同数据源。"""
    import dp_cli.decide_train as dt
    import dp_cli.session as sess_mod
    monkeypatch.setattr(
        sess_mod, 'load_refs',
        lambda s: {'2': {'role': 'button', 'name': '登录'}})
    monkeypatch.setattr(
        sess_mod, 'load_session',
        lambda s: {'refs_epoch': {'url': 'https://x/inner'}})
    res = _hard_result(0.4, 0.3,
                       probs={'target': {'2': 0.4, '9': 0.1}})
    dt._maybe_log_hard_case(res, 's1', goal='登录', state=None)
    rows = _hard_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]['url'] == 'https://x/inner'
    assert rows[0]['candidates'][0]['name'] == '登录'


def test_hard_case_concurrent_writes_no_tear(tmp_path):
    """线程锁+跨进程 spawn-lock 双保险——并发 append 不撕裂
    （每行都是完整 JSON，行数齐全）。"""
    import threading
    import dp_cli.decide_train as dt
    res = _hard_result(0.2, 0.05)

    def _w(tag):
        for i in range(40):
            dt._maybe_log_hard_case(dict(res), f's-{tag}',
                                    goal=f'g{tag}-{i}',
                                    state=_HARD_STATE)

    ts = [threading.Thread(target=_w, args=(t,)) for t in 'ab']
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    rows = _hard_rows(tmp_path)     # 撕裂行会 JSONDecodeError 挂掉
    assert len(rows) == 80
    assert all(r['triggers'] for r in rows)


def test_hard_case_via_decide_command(tmp_path, monkeypatch):
    """decide 返回路径单挂点全渠道覆盖：高 conf + 低 margin（歧义）
    → AGENT_UNSURE 且 hard_cases 落盘；conftest autouse 已把
    _hard_file 重定向进 tmp_path 不碰真实 HOME。"""
    import dp_cli.agent as am
    from click.testing import CliRunner
    from dp_cli.main import cli
    from dp_cli.commands import _utils
    from tests.conftest import FakePage

    monkeypatch.setattr(am, '_page_state',
                        lambda p, s, max_opts=20, **kw: dict(_HARD_STATE))
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(am, 'load_session', lambda s: {})
    monkeypatch.setattr(am, 'save_session', lambda *a, **k: None)
    monkeypatch.setattr(am, '_ask_questions', lambda *a, **k: (
        {'action': 'click', 'target': 2, 'confidence': 0.9,
         'probs': {'action': {'click': 0.55, 'type': 0.45},
                   'target': {'2': 0.52, '5': 0.48}}}, {}))
    r = CliRunner().invoke(cli, ['decide', '点登录',
                                 '--engine', 'external'])
    assert 'AGENT_UNSURE' in r.output
    rows = _hard_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]['triggers'] == ['low_margin']
    assert rows[0]['unsure'] is True
    assert rows[0]['goal'] == '点登录'
    assert rows[0]['url'] == 'https://x/login'
    assert [c['ref'] for c in rows[0]['candidates']] == [2, 5]
    assert rows[0]['candidates'][0]['name'] == '登录'


def test_review_hard_cases_summary(tmp_path):
    """bench/review_hard_cases：读盘汇总 + review 清单 conf 升序。"""
    import sys, json
    sys.path.insert(0, 'bench')
    from review_hard_cases import load_rows, summarize, review_rows
    f = tmp_path / 'hc.jsonl'
    f.write_text(
        json.dumps({'goal': 'g1', 'conf': 0.4, 'margin': 0.3,
                    'triggers': ['low_conf'], 'engine': 'reranker',
                    'url': 'u1', 'candidates': []}) + '\n'
        + json.dumps({'goal': 'g2', 'conf': 0.9, 'margin': 0.03,
                      'triggers': ['low_margin'],
                      'engine': 'external', 'url': 'u2',
                      'unsure': True, 'candidates': []}) + '\n'
        + 'not-json\n', encoding='utf-8')
    rows, bad = load_rows(f)
    assert len(rows) == 2 and bad == 1
    s = summarize(rows)
    assert s['total'] == 2 and s['unsure'] == 1
    assert s['triggers'] == {'low_conf': 1, 'low_margin': 1}
    rev = review_rows(rows)
    assert rev[0]['goal'] == 'g1'    # conf 0.4 < 0.9 排前
    assert rev[0]['engine_ref'] is None or 'engine_ref' in rev[0]
