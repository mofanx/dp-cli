

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
    assert r['schema_v'] == 2
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
    # 无内嵌 state 时仍标 schema_v=2（写端版本），candidates 退
    # 回 probs top3 计分集——v2 语义=全池"如可录得"
    assert rows[0]['schema_v'] == 2
    assert [c['ref'] for c in rows[0]['candidates']] == [2, 9]


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
                   'target': {'2': 0.501, '5': 0.499}}}, {}))
    r = CliRunner().invoke(cli, ['decide', '点登录',
                                 '--engine', 'external'])
    assert 'AGENT_UNSURE' in r.output
    rows = _hard_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]['triggers'] == ['low_margin']
    assert rows[0]['unsure'] is True
    assert rows[0]['goal'] == '点登录'
    assert rows[0]['url'] == 'https://x/login'
    # schema_v=2：candidates 录全池（state['options'] 3 条全录，
    # 非 probs top-k 截断）——未计分的 ref7 在列但无 score
    assert rows[0]['schema_v'] == 2
    assert [c['ref'] for c in rows[0]['candidates']] == [2, 5, 7]
    assert rows[0]['candidates'][0]['name'] == '登录'
    assert 'score' not in rows[0]['candidates'][2]


def test_hard_case_full_pool_schema_v2(tmp_path):
    """schema_v=2 全池契约：candidates 录 state['options'] 完整集
    （>3 不被 top3 截断）——计分集带 score、未计分池候选 score
    缺省、池序=ref 序；options_total 透传供"池外有遗"判定。"""
    import dp_cli.decide_train as dt
    state = {'state': {'url': 'https://x/list', 'title': 'L'},
             'options': [{'ref': i, 'role': 'link' if i % 3 else 'button',
                          'name': f'项{i}'} for i in range(1, 9)],
             'epoch': 'e1', 'truncated': False, 'options_total': 9}
    # 计分集含游离 ref50（不在池）——按分降序附尾不丢分
    res = _hard_result(0.4, 0.05,
                       probs={'target': {'7': 0.4, '3': 0.35,
                                         '50': 0.2}})
    dt._maybe_log_hard_case(res, 's1', goal='点某项', state=state)
    rows = _hard_rows(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r['schema_v'] == 2 and r['options_total'] == 9
    # 池序保留+游离计分 ref 附尾
    assert [c['ref'] for c in r['candidates']] == list(range(1, 9)) + [50]
    by_ref = {c['ref']: c for c in r['candidates']}
    assert by_ref[3]['score'] == 0.35 and by_ref[7]['score'] == 0.4
    assert 'score' not in by_ref[1]         # 未计分池候选 score 缺省
    assert by_ref[50]['score'] == 0.2       # 游离计分 ref 不丢
    for c in r['candidates']:
        assert set(c) <= {'ref', 'name', 'role', 'score'}


def test_hard_case_pool_cap(tmp_path):
    """options 超 _HARD_POOL_CAP 截到 cap——病理页单行不爆炸。"""
    import dp_cli.decide_train as dt
    state = {'state': {'url': 'https://x/huge'},
             'options': [{'ref': i, 'role': 'link', 'name': f'n{i}'}
                         for i in range(1, dt._HARD_POOL_CAP + 51)],
             'epoch': 'e', 'truncated': True,
             'options_total': dt._HARD_POOL_CAP + 60}
    dt._maybe_log_hard_case(_hard_result(0.3, 0.02), 's1',
                            goal='x', state=state)
    r = _hard_rows(tmp_path)[0]
    assert r['schema_v'] == 2
    assert len(r['candidates']) == dt._HARD_POOL_CAP
    assert r['candidates'][-1]['ref'] == dt._HARD_POOL_CAP
    assert r['options_total'] == dt._HARD_POOL_CAP + 60


def test_review_v1_row_compat_and_schema_v_counter(tmp_path):
    """旧 v1 行（无 schema_v 字段）解析兼容：load_rows/summarize/
    teacher_rows 全通——summarize 按 v 计数分流。"""
    import sys, json
    sys.path.insert(0, 'bench')
    from review_hard_cases import load_rows, summarize, teacher_rows
    f = tmp_path / 'hc.jsonl'
    f.write_text(
        # v1 旧行：无 schema_v、candidates top3 截断形态
        json.dumps({'goal': 'g1', 'conf': 0.4, 'margin': 0.3,
                    'triggers': ['low_conf'], 'engine': 'reranker',
                    'url': 'u1',
                    'candidates': [{'ref': 1, 'role': 'link', 'name': 'a',
                                    'score': 0.4}]}) + '\n'
        # v2 新行：全池候选（未计分无 score）
        + json.dumps({'goal': 'g2', 'conf': 0.3, 'margin': 0.05,
                      'triggers': ['low_conf'], 'engine': 'reranker',
                      'url': 'u2', 'schema_v': 2,
                      'candidates': [
                          {'ref': i, 'role': 'link', 'name': f'n{i}',
                           **({'score': 0.3} if i == 2 else {})}
                          for i in range(1, 6)]}) + '\n',
        encoding='utf-8')
    rows, bad = load_rows(f)
    assert bad == 0 and len(rows) == 2
    s = summarize(rows)
    assert s['schema_v'] == {'v1': 1, 'v2': 1}
    tr = teacher_rows(rows)
    assert len(tr) == 2
    # conf 升序：g2(0.3) 在前、g1(0.4) 在后
    assert tr[0]['schema_v'] == 2 and len(tr[0]['candidates']) == 5
    assert tr[1]['schema_v'] == 1 and tr[1]['candidate_refs'] == [1]


def test_teacher_rows_v2_interference_sampling(tmp_path):
    """v2 全池行 teacher 模板取样：> _TEACHER_CAND_MAX 时计分候选
    +同名/同 role 高干扰优先——非纯前 N 截断（池尾同名 link 挤进
    模板，前排无关 textbox 让位）。"""
    import sys, json
    sys.path.insert(0, 'bench')
    from review_hard_cases import (load_rows, teacher_rows,
                                   _TEACHER_CAND_MAX)
    cands = []
    # 前排塞满异 role 干扰（textbox）——纯前 N 截断时池尾 link 会丢
    for i in range(1, 41):
        cands.append({'ref': i, 'role': 'textbox', 'name': f'tb{i}'})
    # 计分集 3 个 link（分降序 12>7>3）
    scored = {3: 0.2, 7: 0.3, 12: 0.4}
    for ref, sc in scored.items():
        cands[ref - 1] = {'ref': ref, 'role': 'link',
                          'name': f'登录-{ref}', 'score': sc}
    # 池尾：同名高干扰（"登录-12" 与计分集同名）+同 role link
    cands[38] = {'ref': 39, 'role': 'link', 'name': '登录-12'}
    cands[39] = {'ref': 40, 'role': 'link', 'name': '注册-40'}
    f = tmp_path / 'hc.jsonl'
    f.write_text(json.dumps(
        {'goal': '点登录', 'url': 'u', 'conf': 0.3, 'margin': 0.05,
         'triggers': ['low_conf'], 'engine': 'reranker', 'schema_v': 2,
         'candidates': cands}), encoding='utf-8')
    rows, _ = load_rows(f)
    tr = teacher_rows(rows)
    assert len(tr) == 1
    refs = tr[0]['candidate_refs']
    assert len(refs) == _TEACHER_CAND_MAX
    # 计分候选按分降序排头
    assert refs[:3] == [12, 7, 3]
    # 同名高干扰（ref39 池尾）挤进模板——纯前 N 会丢
    assert 39 in refs
    # 同 role link（ref40）在异 role textbox 之前
    assert refs.index(40) < refs.index(1)


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


# ── post_delta 断言可执行性 + promote 零变化降 elig（DATA_SCHEMA 契约）──

def test_record_post_delta_marker(tmp_path, monkeypatch):
    """断言可执行性校验：pre/post 快照齐 → post_delta diff 字段清单
    落盘——url/hash/refs_epoch 任一变化=有证据，全同=[] 空快照；
    缺 pre_state 无基线不写 post_delta。"""
    import dp_cli.decide_train as dt
    import json
    monkeypatch.setenv('DPX_RECORD_DECIDE', '1')
    f = tmp_path / 'r.jsonl'
    monkeypatch.setattr(dt, '_file', lambda: f)
    refs = {'1': {'role': 'link', 'name': 'Sign in'}}
    dt.record('s', ['click', '@e1'], refs,
              pre_state={'url': 'https://x/a', 'refs_epoch': 's1'},
              post_state={'url': 'https://x/b', 'refs_epoch': 's1'})
    dt.record('s', ['click', '@e1'], refs,
              pre_state={'url': 'https://x/a', 'refs_epoch': 's1'},
              post_state={'url': 'https://x/a', 'refs_epoch': 's2'})
    dt.record('s', ['click', '@e1'], refs,
              pre_state={'url': 'https://x/a', 'refs_epoch': 's1'},
              post_state={'url': 'https://x/a', 'refs_epoch': 's1'})
    dt.record('s', ['click', '@e1'], refs,
              post_state={'url': 'https://x/b'})     # 无 pre 基线
    dt.record('s', ['click', '@e1'], refs,
              pre_state={},                          # 空 dict=快照失败
              post_state={'url': 'https://x/b'})     # 非全字段变化伪证据
    rows = [json.loads(l)
            for l in f.read_text(encoding='utf-8').splitlines()]
    assert rows[0]['post_delta'] == ['url']
    assert rows[1]['post_delta'] == ['refs_epoch']
    assert rows[2]['post_delta'] == []
    assert 'post_delta' not in rows[3]
    assert 'post_delta' not in rows[4]


def test_state_delta_semantics():
    """state_delta 判据：非 dict/全同/post 缺字段 → []；
    pre 缺字段而 post 有值按新状态计变化。"""
    from dp_cli.decide_train import state_delta
    assert state_delta(None, {'url': 'u'}) == []
    assert state_delta({'url': 'u'}, 'x') == []
    assert state_delta({'url': 'u'}, {'url': 'u'}) == []
    # post 侧字段缺失不计变化（快照残缺≠状态变）
    assert state_delta({'url': 'u', 'sig': 'a'}, {'url': 'v'}) == ['url']
    # pre 缺字段 post 有值→计变化
    assert state_delta({'url': 'u'},
                       {'url': 'u', 'refs_epoch': 's1'}) == ['refs_epoch']
    # 键序按 _STATE_KEYS（url 在 refs_epoch 前）
    assert state_delta({'url': 'a', 'refs_epoch': 's1'},
                       {'url': 'b', 'refs_epoch': 's2'}) == [
        'url', 'refs_epoch']


def test_promote_recorded_no_change_demoted():
    """post_state 零变化行自动降 elig——URL/hash/refs_epoch/DOM sig
    全同=动作未生效不冒充 verified；post_delta=[] 标记行即使 url
    字段矛盾也短路拒（写端判据 fail closed 优先）。"""
    import sys
    sys.path.insert(0, 'bench')
    from verified_data import promote_recorded, eligible
    refs = [{'ref': 1, 'role': 'link', 'name': 'Sign in'}]
    base = {'goal': 'g', 'chosen': 'click @e1', 'engine_ref': 1,
            'state': {'refs': refs},
            'pre_state': {'url': 'https://x/a'},
            'post_state': {'url': 'https://x/b'}}
    r = promote_recorded(dict(base))
    assert r and eligible(r)          # 正常 url diff 回归
    # 零变化快照（无标记旧行→就地重算路径）
    bad = dict(base); bad['post_state'] = dict(base['pre_state'])
    assert promote_recorded(bad) is None
    # post_delta=[] 标记短路——与 url diff 矛盾也 fail closed
    bad2 = dict(base); bad2['post_delta'] = []
    assert promote_recorded(bad2) is None
    # post_delta 非空但 url 未变→仍不提升（硬证据只剩 url）
    bad3 = dict(base)
    bad3['pre_state'] = {'url': 'https://x/a', 'sig': 'a'}
    bad3['post_state'] = {'url': 'https://x/a', 'sig': 'b'}
    bad3['post_delta'] = ['sig']
    assert promote_recorded(bad3) is None


def test_review_emit_teacher_template(tmp_path):
    """hard_case→teacher 转正接口：--emit-teacher 出待标注模板——
    src/prov/query/candidates 就位，chosen_ref/positive/negatives
    留空待 LLM 填；engine_ref/conf/triggers 透传作标注参考。"""
    import sys, json
    sys.path.insert(0, 'bench')
    from review_hard_cases import load_rows, teacher_rows, main as rmain
    f = tmp_path / 'hc.jsonl'
    f.write_text(json.dumps({
        'goal': '点登录', 'url': 'https://x/login', 'conf': 0.3,
        'margin': 0.05, 'triggers': ['low_conf', 'low_margin'],
        'engine': 'reranker', 'engine_ref': 2, 'session': 's1',
        'ts': 1,
        'candidates': [{'ref': 2, 'role': 'button', 'name': '登录',
                        'score': 0.4},
                       {'ref': 5, 'role': 'link', 'name': '注册',
                        'score': 0.35}]}) + '\n', encoding='utf-8')
    rows, bad = load_rows(f)
    assert bad == 0
    tr = teacher_rows(rows)
    assert len(tr) == 1
    t = tr[0]
    assert t['src'] == 'teacher_hardneg' and t['prov'] == 'teacher_pending'
    assert t['query'] == '点登录' and t['page'] == 'https://x/login'
    assert t['chosen_ref'] is None and t['positive'] is None
    assert t['negatives'] is None
    assert t['candidates'] == ['button 登录', 'link 注册']
    assert t['candidate_refs'] == [2, 5] and t['engine_ref'] == 2
    assert t['triggers'] == ['low_conf', 'low_margin']
    # 待标注模板不被 eligible() 误收（src 非 verified_real）
    from verified_data import eligible
    assert not eligible(t)
    # CLI 形态落盘
    out = tmp_path / 'teacher.jsonl'
    rmain(['--file', str(f), '--emit-teacher', str(out)])
    disk = json.loads(out.read_text(encoding='utf-8').splitlines()[0])
    assert disk == t


# ── teacher_pending → teacher 显式确认门（--promote-teacher）──────

def _pending_row(**kw):
    r = {'src': 'teacher_hardneg', 'prov': 'teacher_pending',
         'page': 'https://x/login', 'goal': '点登录', 'query': '点登录',
         'chosen_ref': None, 'positive': None, 'negatives': None,
         'candidates': ['button 登录', 'link 注册'],
         'candidate_refs': [2, 5], 'engine_ref': 2}
    r.update(kw)
    return r


def test_promote_teacher_rejects_unfilled(tmp_path):
    """chosen_ref 空/非法/negatives 空 → 一律拒收计数，prov 不改。"""
    import sys
    sys.path.insert(0, 'bench')
    from review_hard_cases import promote_teacher
    cases = [
        _pending_row(),                                        # 未回填
        _pending_row(chosen_ref='', negatives=['link 注册']),  # 空串
        _pending_row(chosen_ref=9, negatives=['link 注册']),   # 不在候选集
        _pending_row(chosen_ref=2),                            # negatives 空
        _pending_row(chosen_ref=2, negatives='link 注册'),     # 非 list
        _pending_row(chosen_ref=2, negatives=['button 登录']), # pos∈negs
        _pending_row(prov='teacher', chosen_ref=2,
                     negatives=['link 注册']),                 # 非 pending 态
    ]
    ok, no = promote_teacher(cases)
    assert ok == [] and len(no) == 7
    reasons = [w for _, w in no]
    assert 'chosen_ref 空' in reasons
    assert 'chosen_ref 不在 candidate_refs' in reasons
    assert 'negatives 空' in reasons
    assert 'prov 非 teacher_pending' in reasons


def test_promote_teacher_valid_row(tmp_path):
    """合法回填行转正：prov='teacher'、chosen_ref 归一、positive
    留空按 chosen 候选渲染回填；数字字符串 ref 可转正。"""
    import sys
    sys.path.insert(0, 'bench')
    from review_hard_cases import promote_teacher
    ok, no = promote_teacher([_pending_row(
        chosen_ref=2, negatives=['link 注册'])])
    assert len(ok) == 1 and not no
    t = ok[0]
    assert t['prov'] == 'teacher' and t['chosen_ref'] == 2
    assert t['positive'] == 'button 登录'          # 自动回填
    assert 'PENDING' not in t['teacher_note']
    # LLM 填成字符串 ref → int 归一转正
    ok2, no2 = promote_teacher([_pending_row(
        chosen_ref='5', positive='link 注册',
        negatives=['button 登录'])])
    assert len(ok2) == 1 and ok2[0]['chosen_ref'] == 5


def test_promote_teacher_cli_and_merge_gate(tmp_path):
    """CLI：--promote-teacher+--promote-out 只写转正行，拒收行计数；
    merge 侧 teacher_pending 天然被 eligible() 拒（src 非
    verified_real）——手改 prov 未过门也进不了默认训练集。"""
    import sys, json
    sys.path.insert(0, 'bench')
    from review_hard_cases import main as rmain
    src = tmp_path / 'pending.jsonl'
    src.write_text(
        json.dumps(_pending_row(chosen_ref=2,
                                negatives=['link 注册'])) + '\n'
        + json.dumps(_pending_row(query='空标注')) + '\n',
        encoding='utf-8')
    out = tmp_path / 'teacher.jsonl'
    rmain(['--promote-teacher', str(src), '--promote-out', str(out)])
    lines = out.read_text(encoding='utf-8').splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])['prov'] == 'teacher'
    # merge 默认门：teacher_pending 行 src=teacher_hardneg 不过
    # eligible() → src 拒收；即使手改 prov='teacher' 同样被拒
    from merge_train import merge
    msrc = tmp_path / 'm.json'
    msrc.write_text(json.dumps([
        json.loads(lines[0]),
        _pending_row(query='伪造行', prov='teacher',
                     chosen_ref=2, negatives=['link 注册']),
    ]), encoding='utf-8')
    merge([str(msrc)], str(tmp_path / 'mout.json'))
    assert json.loads(
        (tmp_path / 'mout.json').read_text(encoding='utf-8')) == []
