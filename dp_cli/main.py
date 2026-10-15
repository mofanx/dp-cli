# -*- coding:utf-8 -*-
"""
dp-cli —— DrissionPage 命令行工具
比 playwright-cli 更强大，充分利用 DrissionPage 的独特优势：
  - 不基于 webdriver，天然反检测
  - 支持浏览器模式 + HTTP 模式无缝切换
  - 强大的定位语法（比 a11y ref 更稳定）
  - lxml 高效批量解析，snapshot 一次 CDP 调用
  - 支持 shadow-root / iframe 穿透
  - 内置网络包监听能力
"""
import click

from dp_cli.commands import register_all

CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'], max_content_width=100)


@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.version_option(None, '-V', '--version', message='%(version)s')
@click.option('-v', '--verbose', is_flag=True, envvar='DP_DEBUG',
              help='输出 debug 日志到 stderr（也可设环境变量 DP_DEBUG=1）')
@click.option('--compact', is_flag=True, envvar='DP_CLI_COMPACT',
              help='紧凑单行 JSON 输出（也可设环境变量 DP_CLI_COMPACT=1）')
@click.pass_context
def cli(ctx, verbose, compact):
    """
    \b
    dp-cli —— DrissionPage 命令行工具

    \b
    快速开始:
      dp open https://example.com
      dp snapshot
      dp click "text:登录"
      dp fill "@name=username" admin
      dp close

    \b
    agent 文档:
      dp catalog --md          完整命令表（实时生成，永不过期）
      dp install --skills      安装 SKILL.md 到 ~/.claude/skills/
    """
    if verbose:
        from dp_cli.logs import set_debug
        set_debug(True)
    if compact:
        from dp_cli.output import set_compact
        set_compact(True)
    from dp_cli.skill_dist import warn_drift
    warn_drift()
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


register_all(cli)


def main():
    cli()


if __name__ == '__main__':
    main()
