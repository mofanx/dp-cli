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
