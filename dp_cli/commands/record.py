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
    """获取浏览器当前激活的标签页用于录制（忽略 session 中的 active_tab 绑定）"""
    from dp_cli.session import get_browser
    page = get_browser(session)
    
    # 使用 CDP 找到当前真正激活的标签页
    try:
        result = page.run_cdp('Target.getTargets')
        for target in result.get('targetInfos', []):
            if target.get('attached') and target.get('type') == 'page':
                target_id = target.get('targetId')
                # 通过 targetId 获取对应的 tab
                for tab in [page.get_tab(tid) for tid in _get_all_tab_ids(page)]:
                    if tab and tab.tab_id == target_id:
                        return tab
    except Exception as e:
        print(f'[record] Failed to get active tab via CDP: {e}', file=__import__('sys').stderr)
    
    # 回退到 latest_tab
    try:
        return page.latest_tab or page
    except Exception:
        return page


def _get_all_tab_ids(page):
    """获取所有标签页的 ID 列表"""
    try:
        # 使用 CDP 获取所有目标
        result = page.run_cdp('Target.getTargets')
        return [
            t.get('targetId') for t in result.get('targetInfos', [])
            if t.get('type') == 'page'
        ]
    except Exception:
        return []


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
                  help='开始前清空已有录制记录（默认已清空，保留兼容）')
    @click.option('--append', is_flag=True, default=False,
                  help='追加到已有录制记录，不清空历史动作')
    def record_start(session, clear_first, append):
        """开始录制当前页面的人工操作。"""
        page = _get_active_page_for_record(session)
        try:
            if clear_first or not append:
                clear_recorded_actions(page)
            status = inject_recorder(page)
            sess = load_session(session) or {}
            sess['recording'] = True
            tab_id = getattr(page, 'tab_id', None)
            if tab_id:
                sess['active_tab'] = tab_id
                sess['recording_tab'] = tab_id
            sess['recording_start_url'] = getattr(page, 'url', None)

            # 导航检测由 JS 端 recordPageEntry() 处理
            # 依赖 performance.navigation API 自动检测 navigate/reload/back_forward
            save_session(session, sess)
            data = dict(status or {})
            data['tab'] = _page_info(page)
            msg = (f'操作录制已开始，已绑定标签页: {data["tab"]["url"][:40]}...\n'
                   f'  提示: 如果绑定不正确，请先执行 "dp tab-select <序号>" 选择正确标签，再录制')
            ok(data, msg=msg)
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
            # 获取页面内操作记录（JS 录制的 click/fill/scroll/press 等）
            actions = stop_recorder(page)

            sess = load_session(session) or {}
            sess['recording'] = False
            sess.pop('recording_tab', None)
            sess.pop('recording_start_url', None)
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
            sess = load_session(session) or {}
            data['recording'] = bool(sess.get('recording'))
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
