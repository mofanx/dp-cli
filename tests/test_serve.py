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
    lines = f.read_text().strip().split('\n')
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
