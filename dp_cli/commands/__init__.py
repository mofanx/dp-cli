# -*- coding:utf-8 -*-
from dp_cli.commands import (
    browser, snapshot_cmd, element, keyboard,
    page, tab, storage, network, record, misc,
    assert_cmd, emulate, observe, batch, rpc, extract, hook, serve,
    meta,
)

_MODULES = [browser, snapshot_cmd, element, keyboard, page, tab, storage, network, record, misc,
            assert_cmd, emulate, observe, batch, rpc, extract, hook, serve,
            meta]


def register_all(cli):
    from dp_cli import daemon as daemon_mod  # 惰性导入，防循环
    for mod in _MODULES + [daemon_mod]:
        mod.register(cli)
