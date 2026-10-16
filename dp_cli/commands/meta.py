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


def check_dp_entry():
    """检测 PATH 上 `dpx` 主入口与 `dp` 兼容入口的归属。

    DrissionPage 4.1.1.4+ 也注册了 `dp` 命令（浏览器路径配置工具，
    非自动化 CLI）——pip 装包/升级顺序可静默抢占；`dpx` 为独占命名。
    返回 {'dpx': {...}, 'dp': {...}}，各含 status/owner/fix。
    """
    return {'dpx': _entry_status('dpx'), 'dp': _entry_status('dp')}


def _entry_status(name: str) -> dict:
    import shutil
    p = shutil.which(name)
    if not p:
        return {'status': 'missing', 'path': None,
                'fix': 'pip 安装 dp-cli 或检查 PATH'}
    try:
        head = open(p, encoding='utf-8', errors='ignore').read(4096)
    except OSError as e:
        return {'status': 'unreadable', 'path': p, 'fix': str(e)}
    if 'dp_cli' in head:
        return {'status': 'ours', 'path': p}
    return {'status': 'stolen', 'path': p,
            'owner': 'DrissionPage' if 'DrissionPage' in head else 'unknown',
            'fix': 'pip install --force-reinstall dp-cli；'
                   '或改用 dpx / python -m dp_cli.main'}


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
          dpx install --skills                 # ~/.claude/skills/dp-cli
          dpx install --skills --all           # 全部已知 harness 目录探测
          dpx install --skills --dir ./.cursor # 自定义根目录
          dpx install --skills --check         # 校验 bundled vs installed
          dpx install --skills --agents-md     # 顺带更新 ./AGENTS.md 指针段
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
            copies, cur, drift, diffs = check_drift()
            ep = check_dp_entry()
            stolen = {n: e for n, e in ep.items()
                      if e.get('status') == 'stolen'}
            ep_warn = ('；⚠ 入口被抢占: '
                       + ','.join(f'{n}→{e["path"]}' for n, e in stolen.items())
                       + ' ' + next(iter(stolen.values()))['fix']) if stolen else ''
            # 内容不一致 或 任一已装副本版本戳≠CLI 版本都算漂移
            stale = [str(t) for t in targets
                     if not skill_dirs_equal(src, t)]
            if stale or drift:
                error(f'skill 漂移或未安装: stale={stale}'
                      f' installed={ {str(t): v for t, v in copies.items()} }'
                      f' cli={cur}',
                      code='SKILL_DRIFT',
                      detail='修复: dpx install --skills' + ep_warn
                             + ('；内容差异: '
                                + json.dumps({str(k): v
                                              for k, v in diffs.items()},
                                             ensure_ascii=False)
                                if diffs else ''))
                return
            ok({'targets': [str(t) for t in targets],
                'cli_version': cur,
                'entrypoint': ep,
                'installed': {str(t): v for t, v in copies.items()}},
               msg='skill 与包内副本一致' + ep_warn)
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
          dpx catalog          # tool schema JSON
          dpx catalog --md     # commands.md 风格命令表
        """
        from dp_cli.catalog import iter_commands, to_markdown
        cmds = list(iter_commands())
        if as_md:
            click.echo(to_markdown(cmds), nl=False)
            return
        tools = [{k: v for k, v in c.items() if not k.startswith('_')}
                 for c in cmds]
        click.echo(json.dumps({'tools': tools}, ensure_ascii=False, indent=2))
