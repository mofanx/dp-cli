# -*- coding:utf-8 -*-
"""X-* 命令族 → dpx X <sub> 等价子命令组（参数/回调复用，不复制装饰器）。"""
import click


def register_alias_groups(cli):
    """扫描 cli.commands，为 ≥2 成员的 X-* 族生成 Group。

    - 旧 X-* 平级命令全保留（向后兼容）；
    - 子命令复用源 Command 的 params/callback/context_settings；
    - 前缀已是命令/group 名（如 tab）时跳过——避免覆盖；
    - 必须在所有 register() 之后调用。
    """
    families = {}
    for name, cmd in cli.commands.items():
        if '-' not in name:
            continue
        prefix, sub = name.split('-', 1)
        families.setdefault(prefix, []).append((sub, cmd))

    for prefix, members in sorted(families.items()):
        if len(members) < 2 or prefix in cli.commands:
            continue
        grp = click.Group(
            prefix,
            help=f'{prefix} 命令组（{prefix}-* 的等价写法）')
        for sub, src in members:
            grp.add_command(click.Command(
                sub,
                callback=src.callback,
                params=list(src.params),
                help=src.help,
                context_settings=src.context_settings))
        cli.add_command(grp)
