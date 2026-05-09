# -*- coding:utf-8 -*-
from dp_cli.commands import (
    browser, snapshot_cmd, element, keyboard,
    page, tab, storage, network, record, misc,
)

_MODULES = [browser, snapshot_cmd, element, keyboard, page, tab, storage, network, record, misc]


def register_all(cli):
    for mod in _MODULES:
        mod.register(cli)
