# -*- coding:utf-8 -*-
"""B19：X-* 命令族 → dpx X <sub> 等价子命令组。

设计：参数/回调复用现有 Command（params/callback 共享），
不复制装饰器逻辑；旧 X-* 平级命令全保留。
daemon 路由注意：首参为 group 名（如 'record'）时，成员全为
本地命令的族必须整体跳过 daemon 转发。
"""
import json

from click.testing import CliRunner

import dp_cli.commands.storage as storage_mod


class _Cookies(dict):
    def as_dict(self):
        return dict(self)


class _FakePage:
    def cookies(self, **kw):
        return _Cookies(session_id='abc123')


def _cookie_mocks(monkeypatch):
    monkeypatch.setattr(storage_mod, '_get_page', lambda s, **kw: _FakePage())


def test_b19_group_structure():
    """cookie 族生成 Group，子命令名 = X-* 的第二段，params 与源命令一致。"""
    import click
    from dp_cli.main import cli
    grp = cli.commands.get('cookie')
    assert isinstance(grp, click.Group)
    for sub in ('list', 'get', 'set', 'delete', 'clear', 'export'):
        assert sub in grp.commands, f'cookie {sub} 缺失'
    src = cli.commands['cookie-get']
    sub = grp.commands['get']
    assert [p.name for p in sub.params] == [p.name for p in src.params]


def test_b19_cookie_get_group_vs_flat(monkeypatch):
    """group 与平级命令输出一致。"""
    _cookie_mocks(monkeypatch)
    from dp_cli.main import cli
    r1 = CliRunner().invoke(cli, ['cookie-get', 'session_id'])
    r2 = CliRunner().invoke(cli, ['cookie', 'get', 'session_id'])
    assert json.loads(r1.output)['status'] == 'ok'
    assert r1.output == r2.output


def test_b19_group_no_sub_shows_help():
    """dpx cookie（无子命令）→ usage 提示（click 标准 exit 2）而非崩溃。"""
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['cookie'])
    assert r.exit_code == 2
    assert 'cookie' in r.output


def test_b19_all_families_have_groups():
    """≥2 成员的 X-* 族生成 alias group。

    decide/api/model 前缀与既有命令同名（decide GOAL、api、model pull）——
    按设计跳过，不产生歧义 group；其 X-* 平级命令不受影响。
    """
    import click
    from dp_cli.main import cli
    expected = {
        'cookie': {'list', 'get', 'set', 'delete', 'clear', 'export'},
        'localstorage': {'list', 'get', 'set', 'delete', 'clear'},
        'sessionstorage': {'list', 'get', 'set', 'delete', 'clear'},
        'assert': {'count', 'text', 'url', 'visible', 'value'},
        'rpc': {'register', 'call', 'list', 'unregister'},
        'hook': {'set', 'list', 'clear', 'dump'},
        'dialog': {'accept', 'dismiss', 'policy'},
        'daemon': {'start', 'status', 'stop'},
        'state': {'save', 'load'},
        'go': {'back', 'forward'},
        'http': {'get', 'post'},
        'clipboard': {'get', 'set'},
        'console': {'listen', 'dump'},
        'record': {'start', 'stop', 'show', 'clear', 'status', 'export'},
        'tab': {'list', 'new', 'select', 'close'},
        'agent': {'setup', 'state'},
    }
    for grp_name, subs in expected.items():
        grp = cli.commands.get(grp_name)
        assert isinstance(grp, click.Group), f'{grp_name} 非 Group'
        assert subs <= set(grp.commands), \
            f'{grp_name} 缺子命令: {subs - set(grp.commands)}'
    # 冲突前缀不生成 alias group：decide/api 是 Command，model 是自有 Group
    import click as _click
    assert not isinstance(cli.commands['decide'], _click.Group)
    assert not isinstance(cli.commands['api'], _click.Group)
    assert 'status' not in cli.commands['model'].commands


def test_b19_daemon_skip_group_names(monkeypatch):
    """成员全为本地命令的族，group 名也必须 _skipped——
    否则 'dpx record start' 会被当 'record' 转发进 daemon。"""
    from dp_cli import daemon
    for name in ('record', 'daemon', 'model'):
        assert daemon._skipped(name), f'{name} 未被 daemon 跳过'
