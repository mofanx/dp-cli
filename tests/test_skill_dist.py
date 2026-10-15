# -*- coding:utf-8 -*-
"""skill 随包分发 + install --skills + 漂移检测 + catalog 命令化。"""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from dp_cli.main import cli
from dp_cli import skill_dist as sd


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """隔离 HOME：install --skills 默认目标与 drift 检测都走 Path.home()。"""
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    return tmp_path


def _skill_dir(home):
    return home / '.claude' / 'skills' / 'dp-cli'


@pytest.mark.unit
def test_install_skills_default_target(runner, fake_home):
    """默认装到 ~/.claude/skills/dp-cli，frontmatter 写入 CLI 版本戳。"""
    r = runner.invoke(cli, ['install', '--skills'])
    assert r.exit_code == 0
    dst = _skill_dir(fake_home)
    assert (dst / 'SKILL.md').is_file()
    assert (dst / 'references' / 'commands.md').is_file()
    assert sd.skill_version_of(dst / 'SKILL.md') == sd.cli_version()


@pytest.mark.unit
def test_install_skills_idempotent(runner, fake_home):
    """二次安装幂等重写，内容一致。"""
    runner.invoke(cli, ['install', '--skills'])
    dst = _skill_dir(fake_home)
    first = {p.relative_to(dst): p.read_bytes()
             for p in dst.rglob('*') if p.is_file()}
    r = runner.invoke(cli, ['install', '--skills'])
    assert r.exit_code == 0
    second = {p.relative_to(dst): p.read_bytes()
              for p in dst.rglob('*') if p.is_file()}
    assert first == second


@pytest.mark.unit
def test_install_skills_dir_flag(runner, fake_home):
    """--dir 自定义根目录。"""
    r = runner.invoke(cli, ['install', '--skills',
                            '--dir', str(fake_home / 'custom')])
    assert r.exit_code == 0
    assert (fake_home / 'custom' / 'dp-cli' / 'SKILL.md').is_file()


@pytest.mark.unit
def test_install_skills_all_layouts(runner, fake_home, monkeypatch):
    """--all 探测存在的项目级目录全装；都不存在时回落默认。"""
    (fake_home / 'proj' / '.claude' / 'skills').mkdir(parents=True)
    (fake_home / 'proj' / '.agents' / 'skills').mkdir(parents=True)
    monkeypatch.chdir(fake_home / 'proj')
    r = runner.invoke(cli, ['install', '--skills', '--all'])
    assert r.exit_code == 0
    assert (fake_home / 'proj' / '.claude' / 'skills' / 'dp-cli'
            / 'SKILL.md').is_file()
    assert (fake_home / 'proj' / '.agents' / 'skills' / 'dp-cli'
            / 'SKILL.md').is_file()
    assert not (fake_home / 'proj' / '.devin' / 'skills').exists()


@pytest.mark.unit
def test_install_skills_check(runner, fake_home):
    """--check：未装→SKILL_DRIFT exit 1；装后→ok；改版本戳→漂移。"""
    r = runner.invoke(cli, ['install', '--skills', '--check'])
    assert r.exit_code == 1
    assert json.loads(r.output)['code'] == 'SKILL_DRIFT'
    runner.invoke(cli, ['install', '--skills'])
    r = runner.invoke(cli, ['install', '--skills', '--check'])
    assert r.exit_code == 0
    # 改版本戳 → 漂移
    md = _skill_dir(fake_home) / 'SKILL.md'
    md.write_text(md.read_text().replace('dp_cli_version: 0.7.0',
                                         'dp_cli_version: 9.9.9'))
    r = runner.invoke(cli, ['install', '--skills', '--check'])
    assert r.exit_code == 1


@pytest.mark.unit
def test_install_refresh_commands_md(runner, fake_home, monkeypatch):
    """安装时 commands.md 生成段现场重建——bundled 快照陈旧也能对齐。"""
    src = sd.bundled_skill_dir()
    stale = fake_home / 'bundled'
    import shutil
    shutil.copytree(src, stale)
    cmd_md = stale / 'references' / 'commands.md'
    txt = cmd_md.read_text(encoding='utf-8')
    cmd_md.write_text(txt + '\n| 伪造 | `ghost-cmd` | [x] |\n',
                      encoding='utf-8')
    monkeypatch.setattr(sd, 'bundled_skill_dir', lambda: stale)
    from dp_cli.commands import meta
    monkeypatch.setattr(meta, 'bundled_skill_dir', lambda: stale)
    r = runner.invoke(cli, ['install', '--skills'])
    assert r.exit_code == 0
    installed = (_skill_dir(fake_home) / 'references'
                 / 'commands.md').read_text(encoding='utf-8')
    assert 'ghost-cmd' not in installed          # 伪造行被现场生成覆盖
    assert '`open`' in installed                 # 真实命令在


@pytest.mark.unit
def test_warn_drift_stderr_only(fake_home):
    """漂移警告只去 stderr，stdout 纯净；once-per-version 防刷屏。"""
    sep = CliRunner(mix_stderr=False)
    sep.invoke(cli, ['install', '--skills'])
    md = _skill_dir(fake_home) / 'SKILL.md'
    md.write_text(md.read_text().replace('dp_cli_version: 0.7.0',
                                         'dp_cli_version: 0.0.1'))
    (fake_home / '.dp_cli').mkdir(exist_ok=True)
    r = sep.invoke(cli, ['catalog'])
    assert r.exit_code == 0
    assert 'tools' in json.loads(r.stdout)         # stdout 纯 JSON
    assert 'dp install --skills' in r.stderr       # 警告在 stderr
    r2 = sep.invoke(cli, ['catalog'])              # marker 已存在 → 静默
    assert 'install --skills' not in r2.stderr


@pytest.mark.unit
def test_catalog_md_and_json(runner):
    """dp catalog 默认 JSON schema，--md 出命令表（裸输出可重定向）。"""
    r = runner.invoke(cli, ['catalog'])
    assert r.exit_code == 0
    assert json.loads(r.output)['tools']
    r = runner.invoke(cli, ['catalog', '--md'])
    assert 'GENERATED' in r.output and '`open`' in r.output


@pytest.mark.unit
def test_stamp_version_three_states():
    """_stamp_version：替换占位 / 注入 frontmatter / 无 frontmatter。"""
    assert 'dp_cli_version: 1.2.3' in sd._stamp_version(
        '---\ndp_cli_version: "dev"\n---\nx', '1.2.3')
    out = sd._stamp_version('---\nname: dp-cli\n---\nx', '1.2.3')
    assert 'dp_cli_version: 1.2.3\n---' in out
    out = sd._stamp_version('# no frontmatter', '1.2.3')
    assert out.startswith('---\ndp_cli_version: 1.2.3\n---')


@pytest.mark.unit
def test_install_without_skills_flag(runner):
    """dp install 无 --skills → INVALID_ARGS。"""
    r = runner.invoke(cli, ['install'])
    assert r.exit_code != 0
    assert 'INVALID_ARGS' in r.output


# ─────────────────────────────────────────────────────────────────────────────
# --all 全 harness 目标矩阵（t1 扩展）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_all_user_level_targets(runner, fake_home, monkeypatch):
    """--all：用户级 harness 根存在即装；~/.codeium/windsurf 已移除矩阵。"""
    (fake_home / '.claude').mkdir()
    (fake_home / '.cursor').mkdir()
    (fake_home / '.codeium' / 'windsurf').mkdir(parents=True)
    (fake_home / '.config' / 'devin').mkdir(parents=True)
    monkeypatch.chdir(fake_home)          # cwd 无项目级目录
    r = runner.invoke(cli, ['install', '--skills', '--all'])
    assert r.exit_code == 0
    installed = json.loads(r.output)['data']['installed']
    assert len(installed) == 3
    for root in ('.claude', '.cursor', '.config/devin'):
        assert (fake_home / root / 'skills' / 'dp-cli'
                / 'SKILL.md').is_file()
    # .codeium/windsurf 根存在但不在矩阵——不装
    assert not (fake_home / '.codeium' / 'windsurf' / 'skills').exists()


@pytest.mark.unit
def test_all_proj_and_user_mix(runner, fake_home, monkeypatch):
    """--all：项目级 .devin 与用户级 ~/.claude 同时命中；未存在根不建。"""
    (fake_home / '.claude').mkdir()
    (fake_home / 'proj' / '.devin').mkdir(parents=True)
    monkeypatch.chdir(fake_home / 'proj')
    r = runner.invoke(cli, ['install', '--skills', '--all'])
    installed = json.loads(r.output)['data']['installed']
    assert len(installed) == 2
    assert (fake_home / 'proj' / '.devin' / 'skills' / 'dp-cli'
            / 'SKILL.md').is_file()
    assert (fake_home / '.claude' / 'skills' / 'dp-cli'
            / 'SKILL.md').is_file()
    # 未存在的 harness 根不创建目录
    assert not (fake_home / 'proj' / '.windsurf').exists()
    assert not (fake_home / '.agents').exists()


@pytest.mark.unit
def test_all_no_roots_falls_back(runner, fake_home, monkeypatch):
    """--all 无任何已知 harness 根 → 回落默认 ~/.claude/skills。"""
    monkeypatch.chdir(fake_home)
    r = runner.invoke(cli, ['install', '--skills', '--all'])
    assert r.exit_code == 0
    assert (fake_home / '.claude' / 'skills' / 'dp-cli'
            / 'SKILL.md').is_file()


@pytest.mark.unit
def test_roots_cover_major_harnesses():
    """harness 矩阵覆盖主流 agent 目录（Cursor/OpenCode/Codex 等官方路径）。"""
    proj_names = set(sd._PROJ_ROOTS)
    user_names = set(sd._USER_ROOTS)
    # 项目级：Claude/agents 约定/Devin/Windsurf + Cursor/OpenCode
    assert {'.claude', '.agents', '.devin', '.windsurf',
            '.cursor', '.opencode'} <= proj_names
    # 用户级：含 Cursor、Codex legacy、OpenCode、Devin
    assert {'.claude', '.agents', '.cursor', '.codex',
            '.config/devin', '.config/opencode'} <= user_names
    # ~/.codeium/windsurf* 已按用户要求移出矩阵
    assert not any('codeium' in u for u in user_names)


@pytest.mark.unit
def test_installed_copies_requires_skill_md(fake_home, monkeypatch):
    """installed_copies 只统计真有 SKILL.md 的目标（harness 根存在
    但 skill 未装 → 不计入）。"""
    (fake_home / '.claude').mkdir()
    (fake_home / '.config' / 'devin').mkdir(parents=True)
    # 只有 .claude 下有真安装
    dst = fake_home / '.claude' / 'skills' / 'dp-cli'
    dst.mkdir(parents=True)
    (dst / 'SKILL.md').write_text('x', encoding='utf-8')
    copies = sd.installed_copies()
    assert len(copies) == 1 and str(dst) in str(copies.keys())


@pytest.mark.unit
def test_drift_multi_target(runner, fake_home, monkeypatch):
    """drift 扫描全部已装目标：任一目标版本戳过期 → drift=True，
    且能定位到具体漂移目标。"""
    (fake_home / '.claude').mkdir()
    (fake_home / '.config' / 'devin').mkdir(parents=True)
    monkeypatch.chdir(fake_home)
    runner.invoke(cli, ['install', '--skills', '--all'])
    copies, cur, drift = sd.check_drift()
    assert not drift and len(copies) == 2
    # 只改 devin 目标的版本戳 → 仍检出
    md = (fake_home / '.config' / 'devin' / 'skills' / 'dp-cli'
          / 'SKILL.md')
    md.write_text(md.read_text().replace(
        f'dp_cli_version: {cur}', 'dp_cli_version: 0.0.1'))
    copies, cur, drift = sd.check_drift()
    assert drift
    stale = [str(t) for t, v in copies.items() if v != cur]
    assert len(stale) == 1 and 'devin' in stale[0]


@pytest.mark.unit
def test_warn_drift_multi_target_message(fake_home, monkeypatch):
    """多目标漂移：stderr 警告列出漂移目标路径，stdout 纯净。"""
    sep = CliRunner(mix_stderr=False)
    (fake_home / '.claude').mkdir()
    (fake_home / '.config' / 'devin').mkdir(parents=True)
    monkeypatch.chdir(fake_home)
    sep.invoke(cli, ['install', '--skills', '--all'])
    md = (fake_home / '.config' / 'devin' / 'skills' / 'dp-cli'
          / 'SKILL.md')
    md.write_text(md.read_text().replace('dp_cli_version: 0.7.0',
                                         'dp_cli_version: 0.0.1'))
    (fake_home / '.dp_cli').mkdir(exist_ok=True)
    r = sep.invoke(cli, ['catalog'])
    assert 'tools' in json.loads(r.stdout)
    assert '.config' in r.stderr and 'install --skills' in r.stderr
    r2 = sep.invoke(cli, ['catalog'])          # once-per-version 静默
    assert 'install --skills' not in r2.stderr


@pytest.mark.unit
def test_install_agents_md_flag(runner, fake_home, monkeypatch):
    """--agents-md：cwd AGENTS.md 注入 dp-cli 指针段，幂等可重复。"""
    proj = fake_home / 'proj'
    proj.mkdir()
    monkeypatch.chdir(proj)
    # 无 AGENTS.md → created
    r = runner.invoke(cli, ['install', '--skills', '--agents-md'])
    assert r.exit_code == 0
    assert json.loads(r.output)['data']['agents_md'] == 'created'
    md = (proj / 'AGENTS.md').read_text()
    assert 'dp-cli-skill' in md and 'dp catalog --md' in md
    # 二次 → unchanged（幂等）
    r = runner.invoke(cli, ['install', '--skills', '--agents-md'])
    assert json.loads(r.output)['data']['agents_md'] == 'unchanged'
    # 已有其他内容 → 段注入且不破坏既有内容
    (proj / 'AGENTS.md').write_text('# 项目约定\n\n用自己的 lint。\n')
    r = runner.invoke(cli, ['install', '--skills', '--agents-md'])
    assert json.loads(r.output)['data']['agents_md'] == 'updated'
    md = (proj / 'AGENTS.md').read_text()
    assert '用自己的 lint' in md and 'dp-cli-skill' in md


@pytest.mark.unit
def test_install_agents_md_off_by_default(runner, fake_home, monkeypatch):
    """不带 --agents-md → 不动 AGENTS.md（opt-in 边界）。"""
    proj = fake_home / 'proj'
    proj.mkdir()
    monkeypatch.chdir(proj)
    runner.invoke(cli, ['install', '--skills'])
    assert not (proj / 'AGENTS.md').exists()
