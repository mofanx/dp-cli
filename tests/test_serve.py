# -*- coding:utf-8 -*-
"""dp serve / dp sh 常驻 JSONL 协议回归测试"""
import json
import io
import sys

import pytest
from click.testing import CliRunner

from dp_cli.main import cli
import dp_cli.commands.serve as serve_mod
import dp_cli.commands.batch as batch_mod


runner = CliRunner()


def _invoke_serve(stdin_lines, monkeypatch, fake_results=None,
                  session=None):
    """驱动 _serve：mock _invoke + stdin/stdout 注入。"""
    argv_log = []

    def fake_invoke(argv):
        argv_log.append(list(argv))
        if fake_results is not None:
            return fake_results.pop(0)
        return (0, {'status': 'ok', 'echo': argv})

    monkeypatch.setattr(serve_mod, '_invoke', fake_invoke)
    monkeypatch.setattr(sys, 'stdin',
                        io.StringIO('\n'.join(stdin_lines) + '\n'))
    out = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', out)
    code = serve_mod._serve(session=session)
    lines = [json.loads(l) for l in out.getvalue().strip().splitlines()]
    return code, lines, argv_log


@pytest.mark.unit
def test_serve_cmd_string(monkeypatch):
    """{"cmd": "..."} 字符串形态 shlex 解析。"""
    _, lines, argv_log = _invoke_serve(
        ['{"cmd": "open https://x.com"}'], monkeypatch)
    assert argv_log == [['open', 'https://x.com']]
    assert lines[0]['exit'] == 0 and lines[0]['seq'] == 1


@pytest.mark.unit
def test_serve_argv_form(monkeypatch):
    """{"argv": [...]} 数组形态免转义。"""
    _, lines, argv_log = _invoke_serve(
        ['{"argv": ["click", "text:登录"]}'], monkeypatch)
    assert argv_log == [['click', 'text:登录']]


@pytest.mark.unit
def test_serve_id_passthrough(monkeypatch):
    """可选 id 原样透传响应。"""
    _, lines, _ = _invoke_serve(
        ['{"cmd": "snapshot", "id": "req-42"}'], monkeypatch)
    assert lines[0]['id'] == 'req-42'


@pytest.mark.unit
def test_serve_no_id_omitted(monkeypatch):
    """无 id 时响应不含 id 字段。"""
    _, lines, _ = _invoke_serve(['{"cmd": "snapshot"}'], monkeypatch)
    assert 'id' not in lines[0]


@pytest.mark.unit
def test_serve_invalid_json(monkeypatch):
    """非法 JSON → INVALID_ARGS 单行应答，不崩。"""
    _, lines, _ = _invoke_serve(['not-json'], monkeypatch)
    assert lines[0]['exit'] == 2
    assert lines[0]['result']['code'] == 'INVALID_ARGS'


@pytest.mark.unit
def test_serve_missing_cmd_argv(monkeypatch):
    """缺 cmd/argv → INVALID_ARGS。"""
    _, lines, _ = _invoke_serve(['{"foo": 1}'], monkeypatch)
    assert lines[0]['exit'] == 2


@pytest.mark.unit
def test_serve_dp_prefix_stripped(monkeypatch):
    """cmd 含 'dp ' 前缀自动剥离。"""
    _, _, argv_log = _invoke_serve(
        ['{"cmd": "dp snapshot --brief"}'], monkeypatch)
    assert argv_log[0][0] == 'snapshot'


@pytest.mark.unit
def test_serve_nested_forbidden(monkeypatch):
    """嵌套 serve/batch/sh/exec 被拒绝。"""
    _, lines, _ = _invoke_serve(
        ['{"cmd": "serve"}', '{"cmd": "batch -"}',
         '{"cmd": "sh"}', '{"cmd": "exec"}'], monkeypatch)
    assert all(l['exit'] == 2 for l in lines)
    assert all('不允许嵌套' in l['result']['message'] for l in lines)


@pytest.mark.unit
def test_serve_session_inject(monkeypatch):
    """--session 注入无 --session/-S 的子命令。"""
    _, _, argv_log = _invoke_serve(
        ['{"cmd": "snapshot"}',
         '{"cmd": "snapshot --session mine"}'],
        monkeypatch, session='bound')
    assert argv_log[0] == ['snapshot', '--session', 'bound']
    assert argv_log[1] == ['snapshot', '--session', 'mine']  # 已有则不动


@pytest.mark.unit
def test_serve_eof_exits_clean(monkeypatch):
    """EOF 立即优雅退出 0。"""
    code, lines, _ = _invoke_serve([], monkeypatch)
    assert code == 0 and lines == []


@pytest.mark.unit
def test_serve_quit_exits(monkeypatch):
    """quit/exit 行内命令退出。"""
    code, lines, _ = _invoke_serve(['quit'], monkeypatch)
    assert code == 0 and lines == []


@pytest.mark.unit
def test_serve_command_error_isolated(monkeypatch):
    """单命令失败不崩 daemon，后续命令继续。"""
    results = [(1, {'status': 'error', 'code': 'X'}),
               (0, {'status': 'ok'})]
    _, lines, _ = _invoke_serve(
        ['{"cmd": "fail-cmd"}', '{"cmd": "ok-cmd"}'],
        monkeypatch, fake_results=results)
    assert lines[0]['exit'] == 1
    assert lines[1]['exit'] == 0
    assert len(lines) == 2


@pytest.mark.unit
def test_serve_comment_and_blank_skipped(monkeypatch):
    """注释行/空行跳过不计 seq。"""
    _, lines, _ = _invoke_serve(
        ['', '# comment', '{"cmd": "snapshot"}'], monkeypatch)
    assert len(lines) == 1 and lines[0]['seq'] == 1


@pytest.mark.unit
def test_serve_registered():
    """serve/sh 命令注册。"""
    from dp_cli.main import cli as real_cli
    assert 'serve' in real_cli.commands and 'sh' in real_cli.commands


@pytest.mark.unit
def test_serve_isolation_batch_forbidden():
    """batch._FORBIDDEN 已含 serve/sh（防嵌套）。"""
    assert {'serve', 'sh'} <= batch_mod._FORBIDDEN


@pytest.mark.unit
def test_serve_session_injected_only_when_supported(monkeypatch):
    """r1 修复：--session 注入只作用于支持该选项的命令——
    config-set/list/http-* 等无 session 选项的命令不被注入。"""
    _, _, argv_log = _invoke_serve(
        ['{"cmd": "snapshot"}', '{"cmd": "config-set"}',
         '{"cmd": "list"}'],
        monkeypatch, session='my')
    assert argv_log[0] == ['snapshot', '--session', 'my']
    assert argv_log[1] == ['config-set']           # 未注入
    assert argv_log[2] == ['list']                 # 未注入


@pytest.mark.unit
def test_serve_warmup_starts_daemon_thread(monkeypatch):
    """serve 启动时后台线程预 import DrissionPage（daemon，不阻塞主循环）。"""
    rec = {}

    class _T:
        def __init__(self, target=None, daemon=None, **kw):
            rec['target'], rec['daemon'] = target, daemon
        def start(self):
            rec['started'] = True
    monkeypatch.setattr(serve_mod.threading, 'Thread', _T)
    serve_mod._warmup_imports()
    assert rec.get('started') and rec['daemon'] is True
    assert rec['target'] is serve_mod._prewarm


@pytest.mark.unit
def test_prewarm_fault_tolerant(monkeypatch):
    """预热容错：import 失败静默不抛（真用到时由命令自身报错）。"""
    monkeypatch.setitem(sys.modules, 'DrissionPage', None)  # import 必炸
    serve_mod._prewarm()   # 不应抛出


@pytest.mark.unit
def test_serve_command_invokes_warmup(monkeypatch):
    """dp serve 命令体调用预热（EOF 即退的 stdin 下）。"""
    calls = []
    monkeypatch.setattr(serve_mod, '_warmup_imports',
                        lambda: calls.append(1))
    r = CliRunner().invoke(cli, ['serve'], input='')
    assert r.exit_code == 0 and calls == [1]


@pytest.mark.unit
def test_serve_ping_elapsed_contract(monkeypatch):
    """S1-a 契约：ping 零副作用返回+elapsed_ms 全命令覆盖。"""
    from dp_cli.commands.serve import _ping, _emit
    code, r = _ping()
    assert code == 0 and r['pong'] is True and 'version' in r


@pytest.mark.unit
def test_serve_timeout_ms_field():
    """timeout_ms 请求字段解析（_serve 内 json.loads 取）。"""
    import json
    req = json.loads('{"cmd":"eval 1","timeout_ms":500}')
    assert req.get('timeout_ms') == 500


@pytest.mark.unit
def test_repl_tier_fallback(monkeypatch):
    """三层降级：ptk 不可用→readline→裸 input。"""
    import dp_cli.commands.serve as sv
    import builtins
    real_import = builtins.__import__

    # 全不可用 → _repl_bare
    def no_rl(name, *a, **k):
        if name in ('prompt_toolkit', 'readline'):
            raise ImportError
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, '__import__', no_rl)
    monkeypatch.setattr(sv, '_repl_bare', lambda s: 'bare')
    monkeypatch.setattr(sv, '_repl_readline', lambda s: 'rl')
    monkeypatch.setattr(sv, '_repl_ptk', lambda s: 'ptk')
    assert sv._repl('x') == 'bare'


@pytest.mark.unit
def test_repl_downgrade_tip(monkeypatch, capsys):
    """L2/L3 降级进 _repl_bare 打一次 [repl] tip；L1 ptk 不打。"""
    import dp_cli.commands.serve as sv
    monkeypatch.setattr('builtins.input',
                        lambda p='': (_ for _ in ()).throw(EOFError))

    sv._repl_bare('x')
    err = capsys.readouterr().err
    assert err.count('dp-cli[repl]') == 1  # bare 打一次不刷屏

    def fake_rl_import(name, *a, **k):
        if name == 'readline':
            m = type(sys)('readline')
            for fn in ('read_history_file', 'set_completer',
                       'parse_and_bind', 'write_history_file'):
                setattr(m, fn, lambda *a, **k: None)
            return m
        return real_import(name, *a, **k)

    import builtins, sys
    real_import = builtins.__import__
    monkeypatch.setattr(builtins, '__import__', fake_rl_import)
    monkeypatch.setenv('HOME', '/tmp/dpx_test_rl')
    sv._repl_readline('x')
    assert 'dp-cli[repl]' in capsys.readouterr().err


@pytest.mark.unit
def test_exec_line_ping(monkeypatch, capsys):
    """_exec_line ping 直出 pong 不经 _invoke。"""
    import dp_cli.commands.serve as sv
    sv._exec_line('ping', 'x')
    out = capsys.readouterr().out
    assert 'pong' in out


@pytest.mark.unit
def test_serve_id_echo_backward_compat():
    """id 回显契约：带 id 回显、无 id 行序不变（向后兼容）。"""
    from dp_cli.commands.serve import _emit
    import io, sys as _s, json as _j
    buf = io.StringIO()
    old = _s.stdout
    _s.stdout = buf
    try:
        _emit(1, 42, 0, {'status': 'ok'})
        _emit(2, None, 0, {'status': 'ok'})
    finally:
        _s.stdout = old
    l1, l2 = buf.getvalue().splitlines()
    assert _j.loads(l1)['id'] == 42
    assert 'id' not in _j.loads(l2)


@pytest.mark.unit
def test_warmup_attach_failure_safe(monkeypatch):
    """_warmup_attach 失败静默——serve 照常可用。"""
    from dp_cli.commands.serve import _warmup_attach
    monkeypatch.setattr('dp_cli.session.get_browser',
                        lambda s: (_ for _ in ()).throw(
                            RuntimeError('no browser')))
    _warmup_attach('x')   # 不抛 = 通过


@pytest.mark.unit
def test_refresh_refs_contract(tmp_path, monkeypatch):
    """@e 补全缓存契约：snapshot 覆盖/导航清空/无缓存空。"""
    import dp_cli.commands.serve as sv
    monkeypatch.setattr('dp_cli.session.load_refs',
                        lambda s: {'3': {'role': 'button',
                                         'name': '提交'}})
    sv._REF_CACHE = {}
    sv._refresh_refs('snapshot', 'x')
    assert sv._REF_CACHE['3']['role'] == 'button'
    sv._refresh_refs('goto', 'x')
    assert sv._REF_CACHE == {}
    sv._refresh_refs('click', 'x')
    assert sv._REF_CACHE == {}


@pytest.mark.unit
def test_e_completer_candidates():
    """ptk @e 补全：候选+meta 显示 role/name。"""
    import dp_cli.commands.serve as sv
    sv._REF_CACHE = {'3': {'role': 'button', 'name': '提交'},
                     '12': {'role': 'link', 'name': '首页'}}
    pytest.importorskip("prompt_toolkit")
    from prompt_toolkit.document import Document
    # 直接构造 _DpCompleter 等价逻辑
    import re
    doc = Document('click @e')
    m = re.search(r'@e(\d*)$', doc.text_before_cursor)
    assert m
    found = [rid for rid in sv._REF_CACHE
             if rid.startswith(m.group(1))]
    assert set(found) == {'3', '12'}


@pytest.mark.unit
def test_nav_cmds_tab_invalidation():
    """tab-* 切换使 ref 缓存失效（除 tab-list 只读）。"""
    import dp_cli.commands.serve as sv
    sv._REF_CACHE = {'1': {'role': 'a', 'name': 'x'}}
    for cmd in ('tab-new', 'tab-select', 'tab-close', 'open',
                'goto', 'go-back', 'go-forward', 'reload', 'close'):
        sv._REF_CACHE = {'1': {}}
        sv._refresh_refs(cmd, 'x')
        assert sv._REF_CACHE == {}, cmd
    # 只读/DOM 内变更不清
    for cmd in ('tab-list', 'click', 'fill', 'eval', 'snapshot'):
        sv._REF_CACHE = {'1': {}}
        sv._refresh_refs(cmd, 'x')
        if cmd != 'snapshot':
            assert sv._REF_CACHE == {'1': {}}, cmd


@pytest.mark.unit
def test_write_err_tty_vs_pipe(monkeypatch):
    """_write_err：TTY→sys.stderr.write；非 TTY→buffer UTF-8 字节（非 ASCII 不炸）。"""
    import io, sys as _s
    from dp_cli.output import _write_err

    class FakeTTY(io.StringIO):
        def isatty(self): return True
    tty = FakeTTY()
    monkeypatch.setattr(_s, 'stderr', tty)
    _write_err('警告中文\n')
    assert '警告中文' in tty.getvalue()

    class FakePipe:
        def __init__(self): self.buffer = io.BytesIO()
        def isatty(self): return False
    pipe = FakePipe()
    monkeypatch.setattr(_s, 'stderr', pipe)
    _write_err('警告中文\n')   # GBK locale 下 text 层会炸，字节写不炸
    assert '警告中文'.encode('utf-8') in pipe.buffer.getvalue()


@pytest.mark.unit
def test_decide_train_record_and_export(tmp_path, monkeypatch):
    """录制管线：env 门控+落盘格式+export pos/neg 对。"""
    import dp_cli.decide_train as dt
    f = tmp_path / 'decide_train.jsonl'
    monkeypatch.setattr(dt, '_file', lambda: f)

    monkeypatch.delenv('DPX_RECORD_DECIDE', raising=False)
    dt.record('s', ['click', '@e2'], {'1': {'role': 'button', 'name': 'x'}})
    assert not f.exists()                       # 默认不录（隐私面）

    monkeypatch.setenv('DPX_RECORD_DECIDE', '1')
    refs = {'1': {'role': 'button', 'name': '取消'},
            '2': {'role': 'button', 'name': '确定'}}
    dt.record('s', ['click', '@e2'], refs)
    dt.record('s', ['ping'], refs)              # skip 集不落盘
    import json
    lines = f.read_text(encoding='utf-8').strip().split('\n')
    assert len(lines) == 1
    d = json.loads(lines[0])
    assert d['chosen'] == 'click @e2' and len(d['state']['refs']) == 2

    pairs = dt.export_pairs(f)
    assert len(pairs) == 1
    assert pairs[0]['positive'] == 'button 确定'
    assert pairs[0]['negatives'] == ['button 取消']
    # query 对齐推理分布——元素 name 代理 goal，非命令串、无 @eN 噪声
    assert pairs[0]['query'] == '确定'
    assert '@e' not in pairs[0]['query']
    assert pairs[0]['src'] == 'command'

    # decide 路径——goal 落盘为 query，chosen_ref 作弱标注正例
    dt.record('s', ['decide', '点确定'], refs,
              goal='点确定', chosen_ref=2)
    pairs = dt.export_pairs(f)
    assert len(pairs) == 2
    assert pairs[1]['query'] == '点确定'
    assert pairs[1]['positive'] == 'button 确定'
    assert pairs[1]['src'] == 'decide'

    # 无 @eN 的命令不成对
    dt.record('s', ['open', 'https://x'], refs)
    assert len(dt.export_pairs(f)) == 2


def test_unsure_no_empty_positive(tmp_path):
    """默认门禁拒收无证据行；显式 --allow-unverified 下 src='unsure'
    行仍整行拒训——其负例（引擎建议）也不入训。"""
    import json, sys
    sys.path.insert(0, 'bench')
    from train_reranker import load_pairs
    rows = [{'query': '点登录', 'positive': '',
             'negatives': ['link 未核验'], 'src': 'unsure'},
            {'query': '点登录', 'positive': 'button 登录',
             'negatives': ['link 首页'], 'src': 'command'}]
    f = tmp_path / 'p.json'
    f.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ValueError, match='--allow-unverified'):
        load_pairs(f)
    pairs = load_pairs(f, allow_unverified=True)
    assert ('点登录', '', 1.0) not in pairs         # 空正例不入训
    assert ('点登录', 'link 未核验', 0.0) not in pairs  # unsure 负例拒训
    assert ('点登录', 'link 首页', 0.0) in pairs     # 普通负例不受影响
    assert ('点登录', 'button 登录', 1.0) in pairs   # 正常正例不受影响


def test_export_pairs_unsure_record_kept_no_pair(tmp_path, monkeypatch):
    """unsure+engine_ref：原始 record 留存供人工核验，export 不产训练对。"""
    import json
    import dp_cli.decide_train as dt
    f = tmp_path / 'decide_train.jsonl'
    monkeypatch.setattr(dt, '_file', lambda: f)
    monkeypatch.setenv('DPX_RECORD_DECIDE', '1')
    refs = {'1': {'role': 'link', 'name': '首页'},
            '2': {'role': 'button', 'name': '登录'}}
    dt.record('s', ['decide', '点登录'], refs,
              goal='点登录', engine_ref=2, unsure=True)
    raw = f.read_text(encoding='utf-8').strip().split('\n')
    assert len(raw) == 1                          # 原始记录留存
    d = json.loads(raw[0])
    assert d['unsure'] is True and d['engine_ref'] == 2
    assert 'chosen_ref' not in d
    assert dt.export_pairs(f) == []               # 不产训练对


def test_merge_train_rejects_unsure_src(tmp_path):
    """merge 默认只收有执行后证据的 verified_real——src='unsure'
    与旧 src='command' 无证据行默认均不入合并，合并结果为空。"""
    import json, sys
    sys.path.insert(0, 'bench')
    from merge_train import merge
    goal = '安全冒烟目标xyzq'      # 非 eval goal 的对照行
    rows = [{'query': goal, 'positive': 'button 登录',
             'negatives': ['link 首页'], 'src': 'unsure'},
            {'query': goal, 'positive': 'button 登录',
             'negatives': ['link 首页'], 'src': 'command'}]
    src = tmp_path / 'in.json'
    out = tmp_path / 'out.json'
    src.write_text(json.dumps(rows, ensure_ascii=False),
                   encoding='utf-8')
    merge([str(src)], str(out))
    merged = json.loads(out.read_text(encoding='utf-8'))
    assert merged == []


def _stub_record_and_refs(monkeypatch):
    """mock record 收集调用 + mock load_refs 记录被查询的 session。

    env 必开——_record_decide_row 有 enabled() 总闸（未开时连
    load_refs IO 都不发生）；_snap_state 置 {} 防其内部 _invoke
    吃掉 fake_results 序列。"""
    rec, refs = [], []
    monkeypatch.setenv('DPX_RECORD_DECIDE', '1')
    monkeypatch.setattr(serve_mod, '_snap_state', lambda s: {})
    monkeypatch.setattr('dp_cli.decide_train.record',
                        lambda *a, **k: rec.append((a, k)))
    monkeypatch.setattr('dp_cli.session.load_refs',
                        lambda s: refs.append(s)
                        or {'1': {'role': 'link', 'name': '首页'}})
    return rec, refs


@pytest.mark.unit
def test_exec_line_decide_records_engine_ref(monkeypatch):
    """_exec_line：decide 成功/AGENT_UNSURE 录 engine_ref（不录 chosen_ref）。"""
    import dp_cli.commands.serve as sv
    rec, _ = _stub_record_and_refs(monkeypatch)
    results = iter([
        (0, {'status': 'ok', 'data': {'ref': 2}}),
        (1, {'status': 'error', 'code': 'AGENT_UNSURE',
             'detail': '{"ref": 2}'}),
        (1, {'status': 'error', 'code': 'AGENT_UNSURE',
             'detail': 'not-json'}),
        (1, {'status': 'error', 'code': 'ERROR', 'message': 'x'}),
    ])
    monkeypatch.setattr(sv, '_invoke', lambda argv: next(results))
    for _ in range(4):
        sv._exec_line('decide 点登录', 'x')
    assert len(rec) == 3                          # 普通 ERROR 不录
    kw = [k for _, k in rec]
    assert kw[0]['engine_ref'] == 2 and kw[0]['unsure'] is False
    assert kw[0]['goal'] == '点登录'
    assert 'chosen_ref' not in kw[0]
    assert kw[1]['engine_ref'] == 2 and kw[1]['unsure'] is True
    assert 'chosen_ref' not in kw[1]
    assert kw[2]['engine_ref'] is None and kw[2]['unsure'] is True


@pytest.mark.unit
def test_serve_decide_records_engine_ref(monkeypatch):
    """_serve：decide 成功/AGENT_UNSURE 录 engine_ref；其他错误不录。"""
    rec, _ = _stub_record_and_refs(monkeypatch)
    results = [
        (0, {'status': 'ok', 'data': {'ref': 2}}),
        (1, {'status': 'error', 'code': 'AGENT_UNSURE',
             'detail': '{"ref": 2}'}),
        (1, {'status': 'error', 'code': 'AGENT_UNSURE',
             'detail': 'not-json'}),
        (1, {'status': 'error', 'code': 'TIMEOUT', 'message': 'x'}),
    ]
    _invoke_serve(['{"cmd": "decide 点登录"}'] * 4, monkeypatch,
                  fake_results=results)
    assert len(rec) == 3                          # TIMEOUT 不录
    kw = [k for _, k in rec]
    assert kw[0]['engine_ref'] == 2 and kw[0]['unsure'] is False
    assert kw[0]['goal'] == '点登录'
    assert 'chosen_ref' not in kw[0]
    assert kw[1]['engine_ref'] == 2 and kw[1]['unsure'] is True
    assert kw[2]['engine_ref'] is None and kw[2]['unsure'] is True


@pytest.mark.unit
def test_serve_success_other_cmds_still_record(monkeypatch):
    """非 decide 成功命令保持原样录制（无 goal/engine_ref）。"""
    rec, _ = _stub_record_and_refs(monkeypatch)
    _invoke_serve(['{"cmd": "click @e1"}',
                   '{"cmd": "open https://x"}'], monkeypatch)
    assert len(rec) == 2
    assert [k for _, k in rec] == [{}, {}]


@pytest.mark.unit
def test_record_session_helper():
    """_record_session：--session/-S 三种形态解析，缺省回落。"""
    from dp_cli.commands.serve import _record_session
    assert _record_session(['click', '@e1', '--session', 'a'], 'b') == 'a'
    assert _record_session(['click', '@e1', '--session=a'], 'b') == 'a'
    assert _record_session(['click', '@e1', '-S', 'a'], 'b') == 'a'
    assert _record_session(['click', '@e1', '-Sa'], 'b') == 'a'
    assert _record_session(['click', '@e1'], 'b') == 'b'
    assert _record_session(['click', '@e1'], None) == 'default'
    assert _record_session(None) == 'default'
    assert _record_session(['click', '--session'], 'b') == 'b'   # 缺值安全


@pytest.mark.unit
def test_exec_line_record_session_override(monkeypatch):
    """REPL 请求内 --session/-S 覆盖启动 session——refs 与 record
    只对请求会话读/写，默认 session 不被查询。"""
    import dp_cli.commands.serve as sv
    rec, refs = _stub_record_and_refs(monkeypatch)
    results = iter([
        (0, {'status': 'ok'}),
        (0, {'status': 'ok'}),
        (0, {'status': 'ok', 'data': {'ref': 2}}),
    ])
    monkeypatch.setattr(sv, '_invoke', lambda argv: next(results))
    sv._exec_line('click @e1 --session other', 'boot')
    sv._exec_line('click @e1 -S other2', 'boot')
    sv._exec_line('decide 点登录 --session other3', 'boot')
    assert refs == ['other', 'other2', 'other3']   # 默认 boot 不被查
    assert [a[0] for a, _ in rec] == ['other', 'other2', 'other3']
    assert rec[2][1]['engine_ref'] == 2


@pytest.mark.unit
def test_serve_record_session_override(monkeypatch):
    """_serve 请求内 --session/-S 覆盖绑定 session——click/decide
    两路径的 load_refs 与 record 都用请求会话。"""
    rec, refs = _stub_record_and_refs(monkeypatch)
    _invoke_serve(['{"cmd": "click @e1 --session other"}',
                   '{"cmd": "decide 点登录 -S other2"}'],
                  monkeypatch,
                  fake_results=[(0, {'status': 'ok'}),
                                (0, {'status': 'ok', 'data': {'ref': 2}})],
                  session='boot')
    assert refs == ['other', 'other2']             # 绑定 boot 不被查
    assert [a[0] for a, _ in rec] == ['other', 'other2']
    assert rec[1][1]['engine_ref'] == 2 and rec[1][1]['unsure'] is False


# ── P1 修复定向测试 ─────────────────────────────────────────

@pytest.mark.unit
def test_serve_timeout_ms_malformed_no_crash(monkeypatch):
    """P1-1：timeout_ms 畸形（str/bool/非正数）→ INVALID_ARGS 应答，
    _serve 主循环不炸，后续命令照常执行。"""
    _, lines, argv_log = _invoke_serve(
        ['{"cmd": "snapshot", "timeout_ms": "abc"}',
         '{"cmd": "snapshot", "timeout_ms": -5}',
         '{"cmd": "snapshot", "timeout_ms": true}',
         '{"cmd": "snapshot"}'], monkeypatch)
    assert [l['exit'] for l in lines] == [2, 2, 2, 0]
    assert all('timeout_ms' in l['result']['message']
               for l in lines[:3])
    assert len(argv_log) == 1                # 只有末条进了 _invoke


@pytest.mark.unit
def test_serve_timeout_arm_disarm_in_finally(monkeypatch):
    """P1-2：合法 timeout_ms → arm 后 finally 复位 setitimer(0)。"""
    import signal
    if not hasattr(signal, 'setitimer'):
        pytest.skip('no setitimer')
    calls = []
    monkeypatch.setattr(signal, 'setitimer',
                        lambda which, val: calls.append(val))
    _, lines, _ = _invoke_serve(
        ['{"cmd": "snapshot", "timeout_ms": 500}'], monkeypatch)
    assert calls == [0.5, 0.0]               # arm 0.5s → 复位 0
    assert lines[0]['exit'] == 0


@pytest.mark.unit
def test_timeout_interrupt_is_base_exception():
    """P1-2：_TimeoutInterrupt 必须 BaseException——Exception 会被
    命令内裸 except 吞掉（timeout 失效）。"""
    from dp_cli.commands.serve import _TimeoutInterrupt
    assert issubclass(_TimeoutInterrupt, BaseException)
    assert not issubclass(_TimeoutInterrupt, Exception)


@pytest.mark.unit
def test_timeout_interrupt_penetrates_except_exception(monkeypatch):
    """P1-2：命令内 except Exception 吞不掉超时——TIMEOUT 应答。"""
    import dp_cli.commands.serve as sv

    def fake_invoke(argv):
        try:
            raise sv._TimeoutInterrupt()
        except Exception:
            return (0, {'status': 'ok', 'swallowed': True})

    monkeypatch.setattr(sv, '_invoke', fake_invoke)
    monkeypatch.setattr(sys, 'stdin',
                        io.StringIO('{"cmd": "snapshot", '
                                    '"timeout_ms": 500}\n'))
    out = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', out)
    sv._serve()
    line = json.loads(out.getvalue().strip().splitlines()[0])
    assert line['result']['code'] == 'TIMEOUT'
    assert 'swallowed' not in line['result']


@pytest.mark.unit
def test_serve_has_session_flag_forms():
    """P1-5：-Sfoo 连写也算已带 session 旗。"""
    from dp_cli.commands.serve import _has_session_flag
    for argv in (['-Sfoo'], ['-S', 'x'], ['--session', 'x'],
                 ['--session=x'], ['--session']):
        assert _has_session_flag(argv), argv
    for argv in (['snapshot'], ['snapshot', '-v'], [], None,
                 ['--sessionx']):
        assert not _has_session_flag(argv), argv


@pytest.mark.unit
def test_serve_session_inject_S_joined(monkeypatch):
    """P1-5：-Sfoo/--session=x 请求不再被追加默认 session
    （双 session 旗缺陷）。"""
    _, _, argv_log = _invoke_serve(
        ['{"cmd": "snapshot -Smine"}',
         '{"cmd": "snapshot --session=mine"}',
         '{"cmd": "snapshot"}'],
        monkeypatch, session='bound')
    assert argv_log[0] == ['snapshot', '-Smine']
    assert argv_log[1] == ['snapshot', '--session=mine']
    assert argv_log[2] == ['snapshot', '--session', 'bound']


@pytest.mark.unit
def test_exec_line_session_inject_S_joined(monkeypatch):
    """P1-5：REPL 路径同样认 -Sfoo 连写。"""
    import dp_cli.commands.serve as sv
    seen = []
    monkeypatch.setattr(sv, '_invoke',
                        lambda a: seen.append(list(a))
                        or (0, {'status': 'ok'}))
    sv._exec_line('snapshot -Smine', 'boot')
    sv._exec_line('snapshot', 'boot')
    assert seen[0] == ['snapshot', '-Smine']
    assert seen[1] == ['snapshot', '--session', 'boot']


@pytest.mark.unit
def test_serve_record_block_fault_tolerant(monkeypatch):
    """P1-9：result 非 dict 不炸 .get；record 抛异常 debug 记录、
    主循环继续服务下一条。"""
    monkeypatch.setenv('DPX_RECORD_DECIDE', '1')
    monkeypatch.setattr(serve_mod, '_snap_state', lambda s: {})
    monkeypatch.setattr('dp_cli.decide_train.record',
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError('boom')))
    _, lines, _ = _invoke_serve(
        ['{"cmd": "snapshot"}', '{"cmd": "snapshot"}'],
        monkeypatch,
        fake_results=[(0, 'not-a-dict'), (0, {'status': 'ok'})])
    assert [l['exit'] for l in lines] == [0, 0]


@pytest.mark.unit
def test_serve_record_block_decide_nondict_result(monkeypatch):
    """P1-9：decide 命令 result 非 dict——isinstance 守卫防
    result.get 炸主循环。"""
    rec, _ = _stub_record_and_refs(monkeypatch)
    _, lines, _ = _invoke_serve(
        ['{"cmd": "decide 点登录"}', '{"cmd": "snapshot"}'],
        monkeypatch,
        fake_results=[(0, ['weird']), (0, {'status': 'ok'})])
    assert [l['exit'] for l in lines] == [0, 0]


@pytest.mark.unit
def test_serve_jsonl_record_carries_pre_state(monkeypatch):
    """断点1：JSONL 渠道录制行带 pre_state——_invoke 前已采的
    执行前快照不再被录制块处 _pre={} 丢弃（此前 serve 录制行
    永缺 pre_state，promote_recorded 要求 pre+post 永不提升）。"""
    rec, _ = _stub_record_and_refs(monkeypatch)
    monkeypatch.setattr(serve_mod, '_snap_state',
                        lambda s: {'url': 'https://x/before'})
    _invoke_serve(['{"cmd": "click @e1"}',
                   '{"cmd": "decide 点登录"}'], monkeypatch,
                  fake_results=[(0, {'status': 'ok'}),
                                (0, {'status': 'ok',
                                     'data': {'ref': 1}})])
    assert len(rec) == 2
    assert rec[0][1]['pre_state'] == {'url': 'https://x/before'}
    assert rec[0][1]['post_state'] == {'url': 'https://x/before'}
    assert rec[1][1]['pre_state'] == {'url': 'https://x/before'}
    assert rec[1][1]['engine_ref'] == 1


@pytest.mark.unit
def test_serve_pre_state_untouched_on_blocked_paths(monkeypatch):
    """断点1 边界：err/forbidden/畸形 timeout 路径不采执行前快照
    ——_pre 保持 {} 且不向录制传 stale 上一命令快照。"""
    rec, _ = _stub_record_and_refs(monkeypatch)
    snaps = []
    monkeypatch.setattr(serve_mod, '_snap_state',
                        lambda s: snaps.append(s) or {'url': 'https://x/'})
    _invoke_serve(['{"cmd": "serve"}',          # _FORBIDDEN → code=2
                   'not-json',                  # parse err → code=2
                   '{"cmd": "snapshot", "timeout_ms": "x"}',
                   '{"cmd": "click @e1"}'], monkeypatch,
                  fake_results=[(0, {'status': 'ok'})])
    # 前三条无执行时机——_snap_state 只为末条真命令采集（pre+post 各一）
    assert len(snaps) == 2
    assert len(rec) == 1
    assert rec[0][0][1][0] == 'click'
    assert rec[0][1]['pre_state'] == {'url': 'https://x/'}


# ── P2：DoS / 契约桶 ──────────────────────────────────────

def _assert_err_contract(result):
    """错误应答契约断言——status/code/message/detail 四字段齐全
    （JSON 契约铁律，缺 code 裸 dict 属回归）。"""
    assert isinstance(result, dict)
    assert result.get('status') == 'error'
    assert isinstance(result.get('code'), str) and result['code']
    assert isinstance(result.get('message'), str) and result['message']
    assert 'detail' in result


@pytest.mark.unit
def test_serve_oversize_line_rejected(monkeypatch):
    """P2-DoS：单行 >1MB 无换行截断 → PAYLOAD_TOO_LARGE 结构化错
    （不整行读入内存），残行丢弃后后续请求照常服务。"""
    big = 'x' * (serve_mod._MAX_REQ_LINE + 10)
    _, lines, argv_log = _invoke_serve(
        [big, '{"cmd": "snapshot"}'], monkeypatch)
    assert lines[0]['exit'] == 2
    assert lines[0]['result']['code'] == 'PAYLOAD_TOO_LARGE'
    _assert_err_contract(lines[0]['result'])
    assert lines[1]['exit'] == 0
    assert argv_log == [['snapshot']]            # 残行未被当命令
    assert [l['seq'] for l in lines] == [1, 2]


@pytest.mark.unit
def test_serve_eof_final_line_no_newline_ok(monkeypatch):
    """P2-DoS 边界：EOF 终结的短行（无换行、长度未达上限）
    仍正常处理——探长逻辑不误伤末行。"""
    monkeypatch.setattr(serve_mod, '_invoke',
                        lambda a: (0, {'status': 'ok', 'echo': a}))
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"cmd": "snapshot"}'))
    out = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', out)
    code = serve_mod._serve()
    lines = [json.loads(l) for l in out.getvalue().strip().splitlines()]
    assert code == 0 and lines[0]['exit'] == 0


@pytest.mark.unit
def test_serve_error_responses_contract(monkeypatch):
    """P2-契约：每条 invalid/异常应答路径均过契约校验——
    status=error + code/message/detail 齐全。"""
    effects = iter([serve_mod._TimeoutInterrupt(),   # → TIMEOUT
                    RuntimeError('boom'),            # → ERROR
                    KeyboardInterrupt()])            # → INTERRUPTED

    def fake_invoke(argv):
        raise next(effects)

    monkeypatch.setattr(serve_mod, '_invoke', fake_invoke)
    stdin_lines = [
        'not-json',                                # INVALID_ARGS（解析）
        '{"foo": 1}',                              # INVALID_ARGS（缺 cmd）
        '{"cmd": "serve"}',                        # INVALID_ARGS（嵌套）
        '{"cmd": "-v"}',                           # INVALID_ARGS（空命令）
        '{"cmd": "snapshot", "timeout_ms": "x"}',  # INVALID_ARGS（畸形）
        '{"cmd": "snapshot", "timeout_ms": 100}',  # TIMEOUT
        '{"cmd": "snapshot"}',                     # ERROR
        '{"cmd": "snapshot"}']                     # INTERRUPTED
    monkeypatch.setattr(sys, 'stdin',
                        io.StringIO('\n'.join(stdin_lines) + '\n'))
    out = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', out)
    serve_mod._serve()
    lines = [json.loads(l) for l in out.getvalue().strip().splitlines()]
    assert len(lines) == 8
    for l in lines:
        _assert_err_contract(l['result'])
    assert [l['result']['code'] for l in lines] == [
        'INVALID_ARGS', 'INVALID_ARGS', 'INVALID_ARGS', 'INVALID_ARGS',
        'INVALID_ARGS', 'TIMEOUT', 'ERROR', 'INTERRUPTED']


@pytest.mark.unit
def test_invoke_nonjson_error_contract():
    """P2-契约：_invoke 非 JSON 错误输出（click 用法错文本）补
    code/message/detail——不再回缺 code 的裸 dict。"""
    code, r = batch_mod._invoke(['snapshot', '--bogus-flag-xyz'])
    assert code == 2
    _assert_err_contract(r)
    assert r['code'] == 'INVALID_ARGS'
    assert r['output']                            # 原始文本保留
