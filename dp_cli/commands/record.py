# -*- coding:utf-8 -*-
"""操作录制命令: record-start / record-stop / record-show / record-clear"""
from pathlib import Path

import click

from dp_cli.commands._utils import session_option, _get_page
from dp_cli.output import ok, error
from dp_cli.session import load_session, save_session
from dp_cli.recorder import (inject_recorder, stop_recorder, get_recorded_actions,
                             clear_recorded_actions, get_recorder_status,
                             format_actions_text, export_actions)


def _get_active_page_for_record(session):
    page = _get_page(session, raw=True, inject_recording=False)
    try:
        tab = page.latest_tab
        return tab or page
    except Exception:
        return page


def _get_recording_page(session):
    page = _get_page(session, raw=True, inject_recording=False)
    sess = load_session(session) or {}
    tab_id = sess.get('recording_tab')
    if tab_id:
        try:
            return page.get_tab(tab_id)
        except Exception:
            pass
    return _get_page(session, inject_recording=False)


def _page_info(page):
    return {
        'id': getattr(page, 'tab_id', None),
        'url': getattr(page, 'url', None),
        'title': getattr(page, 'title', None),
    }


def register(cli):

    @cli.command('record-start')
    @session_option
    @click.option('--clear', 'clear_first', is_flag=True, default=False,
                  help='开始前清空已有录制记录')
    def record_start(session, clear_first):
        """开始录制当前页面的人工操作。"""
        page = _get_active_page_for_record(session)
        try:
            if clear_first:
                clear_recorded_actions(page)
            status = inject_recorder(page)
            sess = load_session(session) or {}
            sess['recording'] = True
            tab_id = getattr(page, 'tab_id', None)
            if tab_id:
                sess['active_tab'] = tab_id
                sess['recording_tab'] = tab_id
            save_session(session, sess)
            data = dict(status or {})
            data['tab'] = _page_info(page)
            ok(data, msg='操作录制已开始，已绑定当前激活标签页')
        except Exception as e:
            error('启动录制失败', code='RECORD_START_FAILED', detail=str(e))

    @cli.command('record-stop')
    @session_option
    @click.option('-f', '--format', 'fmt',
                  type=click.Choice(['dp', 'playwright', 'playwright-async', 'selenium', 'json', 'text']),
                  default='dp', show_default=True, help='输出格式')
    @click.option('-o', '--output', default=None, help='保存输出到文件')
    @click.option('--filename', default=None, help='保存输出到文件（兼容旧参数）', hidden=True)
    @click.option('--raw', is_flag=True, default=False, help='等同于 --format json（兼容旧参数）')
    def record_stop(session, fmt, output, filename, raw):
        """停止录制并输出本次记录。"""
        page = _get_recording_page(session)
        try:
            actions = stop_recorder(page)
            sess = load_session(session) or {}
            sess['recording'] = False
            sess.pop('recording_tab', None)
            save_session(session, sess)
            target_file = output or filename
            if raw:
                fmt = 'json'
            content = format_actions_text(actions, raw=False) if fmt == 'text' else export_actions(actions, fmt)
            if target_file:
                Path(target_file).write_text(content, encoding='utf-8')
                ok({'count': len(actions), 'format': fmt},
                   msg=f'操作录制已停止，共 {len(actions)} 条，已保存到 {target_file}')
            else:
                click.echo(content)
        except Exception as e:
            error('停止录制失败', code='RECORD_STOP_FAILED', detail=str(e))

    @cli.command('record-show')
    @session_option
    @click.option('--raw', is_flag=True, default=False, help='输出完整 JSON 记录')
    @click.option('--filename', default=None, help='保存输出到文件')
    def record_show(session, raw, filename):
        """查看已录制的操作。"""
        page = _get_recording_page(session)
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
        page = _get_recording_page(session)
        try:
            clear_recorded_actions(page)
            ok(msg='录制记录已清空')
        except Exception as e:
            error('清空录制记录失败', code='RECORD_CLEAR_FAILED', detail=str(e))

    @cli.command('record-status')
    @session_option
    def record_status(session):
        """查看录制器状态。"""
        page = _get_recording_page(session)
        try:
            data = get_recorder_status(page)
            data['tab'] = _page_info(page)
            ok(data)
        except Exception as e:
            error('读取录制状态失败', code='RECORD_STATUS_FAILED', detail=str(e))

    @cli.command('record-export')
    @session_option
    @click.option('-f', '--format', 'fmt',
                  type=click.Choice(['dp', 'playwright', 'playwright-async', 'selenium', 'json']),
                  default='dp', show_default=True, help='导出格式')
    @click.option('-o', '--output', default=None, help='保存导出结果到文件')
    @click.option('--filename', default=None, help='保存导出结果到文件（兼容旧参数）', hidden=True)
    def record_export(session, fmt, output, filename):
        """把录制结果导出为脚本。"""
        page = _get_recording_page(session)
        try:
            actions = get_recorded_actions(page)
            exported = export_actions(actions, fmt)
            target_file = output or filename
            if target_file:
                Path(target_file).write_text(exported, encoding='utf-8')
                ok({'count': len(actions), 'format': fmt},
                   msg=f'录制脚本已导出到 {target_file}')
            else:
                click.echo(exported)
        except Exception as e:
            error('导出录制脚本失败', code='RECORD_EXPORT_FAILED', detail=str(e))
