# -*- coding:utf-8 -*-
"""dp batch/exec 脚本模式 + get_browser 进程内缓存的回归测试"""
import io
import json

import pytest
from click.testing import CliRunner

from dp_cli.main import cli
from dp_cli import session


runner = CliRunner()


def _lines(output):
    """把 batch 的 JSONL stdout 解析成 dict 列表（含 summary 行）。"""
    return [json.loads(l) for l in output.strip().splitlines() if l.strip()]


# ── 基础执行与 JSONL 输出 ────────────────────────────────────────────────

@pytest.mark.unit
def test_batch_stdin_sequential_jsonl():
    out = runner.invoke(cli, ['batch'], input='--version\n--version\n')
    assert out.exit_code == 0
    recs = _lines(out.output)
    assert len(recs) == 3                     # 2 条结果 + 1 summary
    assert recs[0]['seq'] == 1 and recs[0]['exit'] == 0
    assert recs[0]['cmd'] == '--version'
    # --version 输出非 JSON → 包装为 output 字段
    assert recs[0]['result']['status'] == 'ok'
    assert 'output' in recs[0]['result']
    assert recs[2]['summary'] == {
        'total': 2, 'executed': 2, 'ok': 2, 'failed': 0,
        'stopped_early': False}


@pytest.mark.unit
def test_batch_comments_blanks_and_dp_prefix():
    src = '# 注释行\n\n   \n  dp --version\n--version\n'
    out = runner.invoke(cli, ['batch'], input=src)
    assert out.exit_code == 0
    recs = _lines(out.output)
    assert recs[0]['line'] == 4              # 原始行号保留
    assert recs[0]['cmd'] == 'dp --version'
    assert recs[1]['line'] == 5
    assert recs[2]['summary']['total'] == 2


@pytest.mark.unit
def test_batch_file_input(tmp_path):
    f = tmp_path / 'cmds.txt'
    f.write_text('--version\n', encoding='utf-8')
    out = runner.invoke(cli, ['batch', str(f)])
    assert out.exit_code == 0
    assert _lines(out.output)[0]['exit'] == 0


@pytest.mark.unit
def test_batch_file_not_found():
    out = runner.invoke(cli, ['batch', '/nonexistent/xyz.txt'])
    assert out.exit_code == 2
    assert 'FILE_NOT_FOUND' in out.output


# ── 失败语义：fail-fast / --keep-going / 退出码透传 ──────────────────────

@pytest.mark.unit
def test_batch_fail_fast_default():
    out = runner.invoke(cli, ['batch'],
                        input='nosuchcmd\n--version\n--version\n')
    assert out.exit_code == 2                # 首个失败码（click 用法错误）
    recs = _lines(out.output)
    assert len(recs) == 2                    # 只执行 1 条 + summary
    assert recs[0]['exit'] == 2
    assert recs[0]['result']['status'] == 'error'
    assert recs[1]['summary']['stopped_early'] is True
    assert recs[1]['summary']['executed'] == 1


@pytest.mark.unit
def test_batch_keep_going():
    out = runner.invoke(cli, ['batch', '-k'],
                        input='nosuchcmd\n--version\nnosuchcmd2\n--version\n')
    assert out.exit_code == 2                # 返回首个失败码
    recs = _lines(out.output)
    assert len(recs) == 5                    # 4 条全执行 + summary
    assert [r['exit'] for r in recs[:4]] == [2, 0, 2, 0]
    s = recs[4]['summary']
    assert s == {'total': 4, 'executed': 4, 'ok': 2, 'failed': 2,
                 'stopped_early': False}


@pytest.mark.unit
def test_batch_nested_forbidden():
    out = runner.invoke(cli, ['batch'], input='batch -\n')
    assert out.exit_code == 2
    rec = _lines(out.output)[0]
    assert rec['result']['code'] == 'INVALID_ARGS'
    assert '嵌套' in rec['result']['message']


@pytest.mark.unit
def test_batch_exec_alias_forbidden_and_works():
    out = runner.invoke(cli, ['exec'], input='--version\nexec x\n')
    assert out.exit_code == 2                # exec 嵌套同样禁止
    recs = _lines(out.output)
    assert recs[0]['exit'] == 0
    assert recs[1]['result']['code'] == 'INVALID_ARGS'


@pytest.mark.unit
def test_batch_shlex_parse_error():
    out = runner.invoke(cli, ['batch'], input='fill "unclosed\n')
    assert out.exit_code == 2
    rec = _lines(out.output)[0]
    assert rec['result']['code'] == 'INVALID_ARGS'
    assert '解析失败' in rec['result']['message']


@pytest.mark.unit
def test_batch_empty_input():
    out = runner.invoke(cli, ['batch'], input='# 只有注释\n\n')
    assert out.exit_code == 2
    assert 'INVALID_ARGS' in out.output or '为空' in out.output


@pytest.mark.unit
def test_batch_tty_stdin_rejected(monkeypatch):
    class FakeTTY(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr('sys.stdin', FakeTTY())
    out = runner.invoke(cli, ['batch'])
    assert out.exit_code == 2
    assert 'INVALID_ARGS' in out.output


@pytest.mark.unit
def test_batch_no_summary():
    out = runner.invoke(cli, ['batch', '--no-summary'], input='--version\n')
    assert out.exit_code == 0
    recs = _lines(out.output)
    assert len(recs) == 1 and 'summary' not in recs[0]


# ── get_browser 进程内缓存 ───────────────────────────────────────────────

class _FakePage:
    tab_ids = ['t1']


@pytest.fixture(autouse=True)
def _clean_cache():
    session._evict_browser_cache()
    yield
    session._evict_browser_cache()


@pytest.mark.unit
def test_get_browser_cache_reuses_attach(monkeypatch):
    calls = []
    monkeypatch.setattr(session, '_connect_browser',
                        lambda *a, **k: calls.append((a, k)) or _FakePage())
    p1 = session.get_browser('cache-test')
    p2 = session.get_browser('cache-test')
    assert p1 is p2
    assert len(calls) == 1                   # 第二次命中缓存，未重连


@pytest.mark.unit
def test_get_browser_explicit_args_bypass_read(monkeypatch):
    calls = []
    monkeypatch.setattr(session, '_connect_browser',
                        lambda *a, **k: calls.append(k) or _FakePage())
    session.get_browser('exp-test', headless=True)
    session.get_browser('exp-test', headless=True)
    assert len(calls) == 2                   # 显式建立参数每次都真实连接
    p = session.get_browser('exp-test')      # 无参命中最后一次写入的缓存
    assert isinstance(p, _FakePage)
    assert len(calls) == 2


@pytest.mark.unit
def test_get_browser_dead_cache_evicted(monkeypatch):
    class _Dead:
        @property
        def tab_ids(self):
            raise RuntimeError('browser gone')

    calls = []
    session._PAGE_CACHE['dead-test'] = _Dead()
    monkeypatch.setattr(session, '_connect_browser',
                        lambda *a, **k: calls.append(1) or _FakePage())
    p = session.get_browser('dead-test')
    assert isinstance(p, _FakePage)
    assert len(calls) == 1                   # 探活失败 → evict → 重连


@pytest.mark.unit
def test_close_browser_evicts_cache(monkeypatch):
    session._PAGE_CACHE['close-test'] = _FakePage()
    monkeypatch.setattr(session, 'load_session', lambda name: {})
    session.close_browser('close-test')
    assert 'close-test' not in session._PAGE_CACHE


@pytest.mark.unit
def test_batch_nested_via_global_flag_blocked():
    """r1 复核发现：'-v batch f.txt' 全局 flag 前置可绕过 argv[0] 嵌套检查。"""
    for line in ('-v batch other.txt', '--verbose batch other.txt',
                 'dp -v exec other.txt'):
        r = runner.invoke(cli, ['batch'], input=line + '\n')
        assert r.exit_code == 2, (line, r.output)
        first = json.loads(r.output.splitlines()[0])
        assert first['result']['code'] == 'INVALID_ARGS', (line, first)
        assert '嵌套' in first['result']['message']
