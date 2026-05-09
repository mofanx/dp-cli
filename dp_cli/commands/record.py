# -*- coding:utf-8 -*-
"""操作录制命令: record-start / record-stop / record-show / record-clear"""
from pathlib import Path

import click

from dp_cli.commands._utils import session_option, _get_page
from dp_cli.output import ok, error
from dp_cli.session import load_session, save_session
from dp_cli.recorder import (inject_recorder, stop_recorder, get_recorded_actions,
                             clear_recorded_actions, get_recorder_status,
                             format_actions_text)


def register(cli):

    @cli.command('record-start')
    @session_option
    @click.option('--clear', 'clear_first', is_flag=True, default=False,
                  help='开始前清空已有录制记录')
    def record_start(session, clear_first):
        """开始录制当前页面的人工操作。"""
        page = _get_page(session)
        try:
            if clear_first:
                clear_recorded_actions(page)
            status = inject_recorder(page)
            sess = load_session(session) or {}
            sess['recording'] = True
            save_session(session, sess)
            ok(status, msg='操作录制已开始')
        except Exception as e:
            error('启动录制失败', code='RECORD_START_FAILED', detail=str(e))

    @cli.command('record-stop')
    @session_option
    @click.option('--filename', default=None, help='保存录制结果到文件')
    @click.option('--raw', is_flag=True, default=False, help='输出完整 JSON 记录')
    def record_stop(session, filename, raw):
        """停止录制并输出本次记录。"""
        page = _get_page(session)
        try:
            actions = stop_recorder(page)
            sess = load_session(session) or {}
            sess['recording'] = False
            save_session(session, sess)
            data = {'count': len(actions), 'actions': actions}
            if filename:
                content = format_actions_text(actions, raw=raw)
                Path(filename).write_text(content, encoding='utf-8')
                ok(data, msg=f'操作录制已停止，共 {len(actions)} 条，已保存到 {filename}')
            else:
                ok(data, msg=f'操作录制已停止，共 {len(actions)} 条')
        except Exception as e:
            error('停止录制失败', code='RECORD_STOP_FAILED', detail=str(e))

    @cli.command('record-show')
    @session_option
    @click.option('--raw', is_flag=True, default=False, help='输出完整 JSON 记录')
    @click.option('--filename', default=None, help='保存输出到文件')
    def record_show(session, raw, filename):
        """查看已录制的操作。"""
        page = _get_page(session)
        try:
            actions = get_recorded_actions(page)
            output = format_actions_text(actions, raw=raw)
            if filename:
                Path(filename).write_text(output, encoding='utf-8')
                ok({'count': len(actions)}, msg=f'录制记录已保存到 {filename}')
            else:
                click.echo(output)
        except Exception as e:
            error('读取录制记录失败', code='RECORD_SHOW_FAILED', detail=str(e))

    @cli.command('record-clear')
    @session_option
    def record_clear(session):
        """清空当前页面的录制记录。"""
        page = _get_page(session)
        try:
            clear_recorded_actions(page)
            ok(msg='录制记录已清空')
        except Exception as e:
            error('清空录制记录失败', code='RECORD_CLEAR_FAILED', detail=str(e))

    @cli.command('record-status')
    @session_option
    def record_status(session):
        """查看录制器状态。"""
        page = _get_page(session)
        try:
            ok(get_recorder_status(page))
        except Exception as e:
            error('读取录制状态失败', code='RECORD_STATUS_FAILED', detail=str(e))
