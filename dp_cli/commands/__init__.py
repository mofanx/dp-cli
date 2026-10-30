# -*- coding:utf-8 -*-
from dp_cli.commands import (
    browser, snapshot_cmd, element, keyboard,
    page, tab, storage, network, record, misc,
    assert_cmd, emulate, observe, batch, rpc, extract, hook, serve,
    meta, model,
)

_MODULES = [browser, snapshot_cmd, element, keyboard, page, tab, storage, network, record, misc,
            assert_cmd, emulate, observe, batch, rpc, extract, hook, serve,
            meta, model]


def register_all(cli):
    from dp_cli import daemon as daemon_mod, mcp as mcp_mod, agent as agent_mod  # 惰性导入
    for mod in _MODULES + [daemon_mod, mcp_mod, agent_mod]:
        mod.register(cli)
