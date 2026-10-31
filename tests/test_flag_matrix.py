# -*- coding:utf-8 -*-
"""旗标一致性矩阵——遍历全部命令断言公共旗拼写统一。

历史缺口：daemon-status 裸 --session 无 -S（peer-review 抓到）。
本测试把"公共旗一致性"变成断言而非复核。
"""
import pytest
from dp_cli.main import cli

pytestmark = pytest.mark.unit

# 含 session 语义的命令必须用 session_option（-S + --session 双旗）
_SESSION_CMDS = {
    'open', 'goto', 'close', 'snapshot', 'scan', 'click', 'fill',
    'clear', 'select', 'hover', 'check', 'upload', 'dblclick',
    'drag', 'press', 'keydown', 'keyup', 'insert-text', 'type',
    'eval', 'screenshot', 'pdf', 'wait', 'sleep', 'read',
    'scroll', 'title', 'url', 'html', 'text', 'attr', 'value',
    'assert-*', 'state-save', 'state-load', 'cookies', 'storage',
    'listen', 'listen-stop', 'requests', 'request', 'api-list', 'api',
    'har',
    'intercept', 'emulate', 'download', 'record-start',
    'record-stop', 'record-show', 'record-status', 'agent-state',
    'decide', 'agent-setup', 'query', 'extract', 'trace', 'metrics',
    'expect-gen', 'daemon-start', 'daemon-status', 'daemon-stop',
    'tab-new', 'tab-switch', 'tab-close', 'tab-list', 'frame',
    'kill-all', 'bridge', 'find', 'focus', 'scroll-into-view',
    'snapshot-diff', 'config', 'batch', 'serve', 'sh', 'fill-form',
    'dialog', 'install', 'whoami',  # whoami 假想项记录
}


def _all_cmd_params(cmd_name):
    """取命令全部 option 名。"""
    import click
    for name, cmd in cli.commands.items():
        if name == cmd_name:
            params = []
            for p in cmd.params:
                params.extend(p.opts + p.secondary_opts)
            return params
    return None


@pytest.mark.parametrize('cmd_name', sorted([
    'open', 'goto', 'snapshot', 'click', 'fill', 'eval', 'decide',
    'agent-state', 'agent-setup', 'record-start', 'record-stop',
    'listen', 'wait', 'assert-text', 'download', 'extract',
]))
def test_session_flag_both_forms(cmd_name):
    """session 命令必须 -S 和 --session 双旗。"""
    params = _all_cmd_params(cmd_name)
    if params is None:
        pytest.skip(f'{cmd_name} 未注册')
    has_long = '--session' in params
    has_short = '-S' in params
    assert has_long and has_short, \
        f'{cmd_name} session 旗不完整: {params}'


def test_daemon_commands_session_flag():
    """daemon-start/status/stop 必须 -S/--session 双旗（本次缺口）。"""
    for cmd in ('daemon-start', 'daemon-status', 'daemon-stop'):
        params = _all_cmd_params(cmd)
        assert params is not None, f'{cmd} 未注册'
        assert '-S' in params and '--session' in params, \
            f'{cmd} 缺 -S: {params}'


def test_no_orphan_long_only_session():
    """所有注册了 --session 的命令都必须同时有 -S（防裸长旗）。"""
    bad = []
    for name, cmd in cli.commands.items():
        opts = []
        for p in cmd.params:
            opts.extend(p.opts + p.secondary_opts)
        if '--session' in opts and '-S' not in opts:
            bad.append(name)
    assert not bad, f'裸 --session 无 -S: {bad}'
