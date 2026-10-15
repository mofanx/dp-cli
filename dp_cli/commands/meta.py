# -*- coding:utf-8 -*-
"""元信息命令：install --skills（skill 安装）/ catalog（命令面导出）"""
import json
from pathlib import Path

import click

from dp_cli.output import ok, error
from dp_cli.skill_dist import (bundled_skill_dir, install_skill,
                               skill_dirs_equal, check_drift,
                               existing_targets)

_SKILL_NAME = 'dp-cli'


def _targets(directory, all_layouts):
    """安装目标列表。--dir > --all 全 harness 探测 > 默认 ~/.claude/skills。"""
    if directory:
        return [Path(directory).expanduser() / _SKILL_NAME]
    if all_layouts:
        found = existing_targets()   # 项目级+用户级全部已知 harness 根
        return found or [Path.home() / '.claude' / 'skills' / _SKILL_NAME]
    return [Path.home() / '.claude' / 'skills' / _SKILL_NAME]


def register(cli):

    @cli.command('install')
    @click.option('--skills', 'with_skills', is_flag=True,
                  help='安装 agent skill 文档（SKILL.md + references/）')
    @click.option('--dir', 'directory', default=None,
                  help='自定义安装根目录（追加 /dp-cli）')
    @click.option('--all', 'all_layouts', is_flag=True,
                  help='探测全部已知 harness 根（Claude/Cursor/Codex/'
                       'OpenCode/Windsurf/Devin/agents 约定，项目级+用户级），'
                       '存在的全装')
    @click.option('--check', 'check_only', is_flag=True,
                  help='只校验不同步：一致 exit 0，漂移 exit 1')
    @click.option('--agents-md', 'agents_md', is_flag=True,
                  help='同时向 ./AGENTS.md 注入/更新 dp-cli 指针段')
    def install(with_skills, directory, all_layouts, check_only, agents_md):
        """安装 dp-cli 组件（对齐 playwright-cli install --skills）。

        \b
        示例:
          dp install --skills                 # ~/.claude/skills/dp-cli
          dp install --skills --all           # 全部已知 harness 目录探测
          dp install --skills --dir ./.cursor # 自定义根目录
          dp install --skills --check         # 校验 bundled vs installed
          dp install --skills --agents-md     # 顺带更新 ./AGENTS.md 指针段
        """
        if not with_skills:
            error('请指定安装内容: --skills（agent 使用文档）',
                  code='INVALID_ARGS')
            return
        src = bundled_skill_dir()
        if src is None:
            error('包内未找到 bundled skill（非 wheel 安装且无 repo 副本）',
                  code='SKILL_NOT_FOUND')
            return
        if check_only:
            targets = _targets(directory, all_layouts)
            copies, cur, drift = check_drift()
            # 内容不一致 或 任一已装副本版本戳≠CLI 版本都算漂移
            stale = [str(t) for t in targets
                     if not skill_dirs_equal(src, t)]
            if stale or drift:
                error(f'skill 漂移或未安装: stale={stale}'
                      f' installed={ {str(t): v for t, v in copies.items()} }'
                      f' cli={cur}',
                      code='SKILL_DRIFT',
                      detail='修复: dp install --skills')
                return
            ok({'targets': [str(t) for t in targets],
                'cli_version': cur,
                'installed': {str(t): v for t, v in copies.items()}},
               msg='skill 与包内副本一致')
            return
        installed = []
        for dst in _targets(directory, all_layouts):
            dst.parent.mkdir(parents=True, exist_ok=True)
            install_skill(dst)
            installed.append(str(dst))
        data = {'installed': installed, 'version': check_drift()[1]}
        if agents_md:
            from dp_cli.skill_dist import update_agents_md
            action = update_agents_md(Path.cwd() / 'AGENTS.md')
            data['agents_md'] = action
        ok(data, msg=f'skill 已安装: {installed[0]}'
               + (f' 等 {len(installed)} 处' if len(installed) > 1 else ''))

    @cli.command('catalog')
    @click.option('--md', 'as_md', is_flag=True, help='markdown 命令表')
    @click.option('--json', 'as_json', is_flag=True,
                  help='MCP 风格 tool schema（默认）')
    def catalog(as_md, as_json):
        """导出完整命令面——无 skill 时 agent 的实时自救文档。

        \b
        示例:
          dp catalog          # tool schema JSON
          dp catalog --md     # commands.md 风格命令表
        """
        from dp_cli.catalog import iter_commands, to_markdown
        cmds = list(iter_commands())
        if as_md:
            click.echo(to_markdown(cmds), nl=False)
            return
        tools = [{k: v for k, v in c.items() if not k.startswith('_')}
                 for c in cmds]
        click.echo(json.dumps({'tools': tools}, ensure_ascii=False, indent=2))
