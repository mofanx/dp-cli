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
@click.version_option(message='%(version)s')
@click.pass_context
def cli(ctx):
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
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


register_all(cli)


def main():
    cli()


if __name__ == '__main__':
    main()
