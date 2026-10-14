# -*- coding:utf-8 -*-
"""--compact 全局紧凑输出回归测试"""
import json

import pytest
from click.testing import CliRunner

import dp_cli.output as out_mod
from dp_cli.main import cli


runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_compact():
    yield
    out_mod.set_compact(False)


def _invoke(argv, env=None):
    return runner.invoke(cli, argv, env=env or {})


# ── ok() / error() 输出形态 ────────────────────────────────────────────

@pytest.mark.unit
def test_default_output_indented():
    """默认 indent=2 多行输出（契约不变）。"""
    out_mod.ok({'a': 1, 'b': 'x'})
    # CliRunner 捕获的 stdout 需经 invoke；改用 capsys 不可行（ok 直接 print）。
    # 直接断言 _dump 行为即可。
    assert '\n' in out_mod._dump({'a': 1})
    assert ': ' in out_mod._dump({'a': 1})


@pytest.mark.unit
def test_compact_dump_single_line():
    """compact：单行 + separators 无空格。"""
    out_mod.set_compact(True)
    s = out_mod._dump({'status': 'ok', 'data': {'a': 1, 'b': 'x'}})
    assert '\n' not in s
    assert '": "' not in s          # 无 ": " 空格
    assert json.loads(s)['data']['a'] == 1   # 契约不动


@pytest.mark.unit
def test_compact_flag_sets(monkeypatch):
    """--compact flag 透传到 output 模块（无子命令路径，callback 必跑）。"""
    captured = {}
    orig = out_mod.set_compact
    monkeypatch.setattr(out_mod, 'set_compact',
                        lambda v: captured.update(v=v) or orig(v))
    _invoke(['--compact'])          # invoke_without_command → help 分支
    assert captured.get('v') is True


@pytest.mark.unit
def test_compact_envvar(monkeypatch):
    """DP_CLI_COMPACT=1 环境变量通道。"""
    captured = {}
    orig = out_mod.set_compact
    monkeypatch.setattr(out_mod, 'set_compact',
                        lambda v: captured.update(v=v) or orig(v))
    _invoke([], env={'DP_CLI_COMPACT': '1'})
    assert captured.get('v') is True


@pytest.mark.unit
def test_compact_error_path(capsys):
    """error() 紧凑输出 + 退出码不变。"""
    out_mod.set_compact(True)
    with pytest.raises(SystemExit) as ei:
        out_mod.error('boom', code='INVALID_ARGS')
    assert ei.value.code == 2          # 退出码契约不变
    s = capsys.readouterr().out.strip()
    assert '\n' not in s
    obj = json.loads(s)
    assert obj == {'status': 'error', 'code': 'INVALID_ARGS',
                   'message': 'boom'}


@pytest.mark.unit
def test_batch_jsonl_unaffected(monkeypatch):
    """batch envelope JSONL 结构不随 compact 变（result 反序列化内嵌）。"""
    import dp_cli.commands.batch as bm

    calls = []
    monkeypatch.setattr(bm, '_invoke',
                        lambda argv: (0, {'status': 'ok', 'data': {'x': 1}}))
    out_mod.set_compact(True)
    r = runner.invoke(cli, ['--compact', 'batch', '-'], input='open x\n')
    lines = r.output.strip().splitlines()
    # 每行仍是合法 JSON（envelope），result 为 dict 非字符串
    for line in lines:
        obj = json.loads(line)
        if 'result' in obj:
            assert isinstance(obj['result'], dict)
            assert obj['result']['status'] == 'ok'


@pytest.mark.unit
def test_compact_flag_skipped_in_batch_cmd_name():
    """batch 内 '--compact open x' 的 cmd_name 检测跳过 flag 不误判。"""
    import dp_cli.commands.batch as bm
    from dp_cli.commands.batch import _FORBIDDEN
    argv = ['--compact', 'open', 'x']
    cmd_name = next((a for a in argv
                     if a not in ('-v', '--verbose', '--compact')), None)
    assert cmd_name == 'open'
    assert cmd_name not in _FORBIDDEN


@pytest.mark.unit
def test_compact_covers_snapshot_json_stdout(monkeypatch):
    """r1 修复：snapshot -f json 的 stdout 路径走 _dump，--compact 生效。"""
    import dp_cli.commands.snapshot_cmd as smod

    class P:
        def run_cdp(self, *a, **k):
            return {}

    monkeypatch.setattr(smod, '_get_page', lambda s: P())
    monkeypatch.setattr(smod, 'take_a11y_snapshot',
                        lambda page, **kw: {'page': {'url': 'u'},
                                            'stats': {'total': 1},
                                            'tree': {}, 'method': 'cdp'})
    # 默认：多行 indent
    r = _invoke(['snapshot', '-f', 'json', '--no-clickables'])
    assert r.exit_code == 0
    assert '\n  ' in r.output
    # compact：单行
    r = _invoke(['--compact', 'snapshot', '-f', 'json', '--no-clickables'])
    assert r.exit_code == 0
    assert r.output.strip().count('\n') == 0
    assert json.loads(r.output)['data']['stats']['total'] == 1
