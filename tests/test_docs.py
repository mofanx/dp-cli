# -*- coding:utf-8 -*-
"""文档腐化守卫：skills/dp-cli/references/commands.md 必须覆盖全部注册命令。

新增命令未入文档时本测试失败——防止手写文档与 click 命令树脱节。
通配写法 `foo-*` 视为覆盖所有 `foo-` 前缀命令（如 localstorage-*）。
"""
import re
from pathlib import Path

from dp_cli.main import cli

_COMMANDS_MD = (Path(__file__).parent.parent
                / 'skills' / 'dp-cli' / 'references' / 'commands.md')


def _documented_commands():
    md = _COMMANDS_MD.read_text(encoding='utf-8')
    tokens = re.findall(r'`([a-z][a-z0-9*-]+)`', md)
    doc = set()
    for t in tokens:
        if t.endswith('*'):
            doc.update(c for c in cli.commands if c.startswith(t[:-1]))
        elif t in cli.commands:
            doc.add(t)
    return doc


def test_all_commands_documented():
    missing = set(cli.commands) - _documented_commands()
    assert not missing, (
        f'commands.md 未覆盖 {len(missing)} 个命令: {sorted(missing)}\n'
        f'请在 {_COMMANDS_MD.name} 命令一览表中补充（或用 `prefix-*` 通配）')


def test_catalog_schema_export():
    """catalog 导出：全部命令可机械转为 tool schema（MCP 上游能力）。"""
    from dp_cli.catalog import iter_commands
    tools = list(iter_commands())
    names = [t['name'] for t in tools]
    assert len(names) == len(cli.commands)
    assert len(set(names)) == len(names)
    for t in tools:
        assert t['inputSchema']['type'] == 'object'
        for req in t['inputSchema']['required']:
            assert req in t['inputSchema']['properties']
